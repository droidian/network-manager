"""
Base class for network related tests.

This provides test ethernet devices with veth, functions to start dnsmasq,
and some utility functions.
"""

__author__ = "Martin Pitt <martin.pitt@ubuntu.com>"
__copyright__ = "(C) 2013-2025 Canonical Ltd."
__license__ = "GPL v2 or later"

import ctypes
import functools
import os
import os.path
import shutil
import subprocess
import tempfile
import time
import traceback
import unittest
from glob import glob

import gi

gi.require_version("NM", "1.0")
from gi.repository import NM, Gio, GLib

# If True, NetworkManager logs directly to stdout, to watch logs in real time
NM_LOG_STDOUT = os.getenv("NM_LOG_STDOUT", False)

# avoid accidentally destroying any real config
os.environ["GSETTINGS_BACKEND"] = "memory"

def run_in_subprocess(fn):
    """Decorator for running fn in a child process"""

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        # args[0] is self
        args[0].wrap_process(fn, *args, **kwargs)

    return wrapped

def wait_nm_online():
    tries = 3
    while tries > 0 and subprocess.call(['nm-online', '-qst', '10']) != 0:
        time.sleep(1)
        tries = tries - 1

def set_up_module():
    # unshare the mount namespace, so that our tmpfs mounts are guaranteed to get
    # cleaned up, and don't influence the production system
    libc6 = ctypes.cdll.LoadLibrary("libc.so.6")
    assert (
        libc6.unshare(ctypes.c_int(0x00020000)) == 0
    ), "failed to unshare mount namespace"

    # stop system-wide NetworkManager to avoid interfering with tests
    subprocess.check_call(['systemctl', 'stop', 'NetworkManager.service'])

def tear_down_module():
    # Make sure the management network stays up-and-running.
    if os.path.exists('/run/systemd/network/20-wired.network'):
        subprocess.check_call(['systemctl', 'restart', 'systemd-networkd.service'])
    else:
        print("WARNING: mgmt network config (20-wired.network) not found. "
              "Skipping restart of systemd-networkd.service ...")


class NetworkTestBase(unittest.TestCase):
    """Common functionality for network test cases

    setUp() creates two test veth devices (self.dev_e_ap and elf.dev_e_client).
    Each test should call self.setup_eth() with the desired configuration.
    """

    @classmethod
    def setUpClass(klass):
        # check availability of programs, and cleanly skip test if they are not
        # available
        for program in ["dnsmasq"]:
            if shutil.which(program) is None:
                raise SystemError("%s is required for this test suite, but not available" % program)

        # Try to keep autopkgtest's management network (eth0/ens3) up and
        # configured. It should be running all the time, independently via
        # systemd-networkd, potentially overriding 10-netplan-*.network config.
        os.makedirs('/run/systemd/network', exist_ok=True)
        with open('/run/systemd/network/20-wired.network', 'w') as f:
            f.write('[Match]\nName=eth0 en*\n\n[Network]\nDHCP=yes\nKeepConfiguration=yes')
        subprocess.check_call(['systemctl', 'restart', 'systemd-networkd.service'])

    @classmethod
    def tearDownClass(klass):
        os.remove("/run/udev/rules.d/99-nm-veth-test.rules")

    @classmethod
    def create_devices(klass):
        """Create Access Point and Client veth devices"""

        klass.dev_e_ap = "veth42"
        klass.dev_e_client = "eth42"

        if os.path.exists("/sys/class/net/" + klass.dev_e_client):
            raise SystemError("%s interface already exists" % klass.dev_e_client)

        # ensure NM can manage our fake eths
        os.makedirs("/run/udev/rules.d", exist_ok=True)
        with open("/run/udev/rules.d/99-nm-veth-test.rules", "w") as f:
            f.write(
                'ENV{ID_NET_DRIVER}=="veth", ENV{INTERFACE}=="%s", ENV{NM_UNMANAGED}="0"\n'
                % klass.dev_e_client
            )
        subprocess.check_call(["udevadm", "control", "--reload"])

        # create virtual ethernet devs
        subprocess.check_call(
            [
                "ip",
                "link",
                "add",
                "name",
                klass.dev_e_client,
                "type",
                "veth",
                "peer",
                "name",
                klass.dev_e_ap,
            ]
        )

        # determine and store MAC addresses
        # Creation of the veths introduces a race with newer versions of
        # systemd, as it  will change the initial MAC address after the device
        # was created and networkd took control. Give it some time, so we read
        # the correct MAC address
        time.sleep(1)
        with open("/sys/class/net/%s/address" % klass.dev_e_ap) as f:
            klass.mac_e_ap = f.read().strip().upper()
        with open("/sys/class/net/%s/address" % klass.dev_e_client) as f:
            klass.mac_e_client = f.read().strip().upper()

    @classmethod
    def shutdown_devices(klass):
        """Remove test wlan devices"""

        subprocess.check_call(["ip", "link", "del", "dev", klass.dev_e_ap])
        klass.dev_e_ap = None
        klass.dev_e_client = None

    def run(self, result=None):
        """Show log files on failed tests"""

        if result:
            orig_err_fail = len(result.errors) + len(result.failures)
        super().run(result)
        if hasattr(self, "workdir"):
            logs = glob(os.path.join(self.workdir, "*.log"))
            if result and len(result.errors) + len(result.failures) > orig_err_fail:
                for log_file in logs:
                    with open(log_file) as f:
                        print(
                            "\n----- %s -----\n%s\n------\n"
                            % (os.path.basename(log_file), f.read())
                        )

            # clean up log files, so that we don't see ones from previous tests
            for log_file in logs:
                os.unlink(log_file)

    def setUp(self):
        """Create test devices and workdir"""

        self.create_devices()
        self.addCleanup(self.shutdown_devices)
        self.workdir_obj = tempfile.TemporaryDirectory()
        self.workdir = self.workdir_obj.name

        # create static entropy file to avoid draining/blocking on /dev/random
        self.entropy_file = os.path.join(self.workdir, "entropy")
        with open(self.entropy_file, "wb") as f:
            f.write(b"012345678901234567890")

    def setup_eth(self, ipv6_mode, start_dnsmasq=True):
        """Set up simulated ethernet router

        On self.dev_e_ap, run dnsmasq according to ipv6_mode, see
        start_dnsmasq().

        This is torn down automatically at the end of the test.
        """
        # give our router an IP
        subprocess.check_call(["ip", "a", "flush", "dev", self.dev_e_ap])
        if ipv6_mode is not None:
            subprocess.check_call(
                ["ip", "a", "add", "2600::1/64", "dev", self.dev_e_ap]
            )
        else:
            subprocess.check_call(
                ["ip", "a", "add", "192.168.5.1/24", "dev", self.dev_e_ap]
            )
        subprocess.check_call(["ip", "link", "set", self.dev_e_ap, "up"])
        # we don't really want to up the client iface already, but veth doesn't
        # work otherwise (no link detected)
        subprocess.check_call(["ip", "link", "set", self.dev_e_client, "up"])

        if start_dnsmasq:
            self.start_dnsmasq(ipv6_mode, self.dev_e_ap)

    def wrap_process(self, fn, *args, **kwargs):
        """Run a test method in a separate process.

        Run test method fn(*args, **kwargs) in a child process. If that raises
        any exception, it gets propagated to the main process and
        wrap_process() fails with that exception.
        """
        # exception from subprocess is propagated through this file
        exc_path = os.path.join(self.workdir, "exc")
        try:
            os.unlink(exc_path)
        except OSError:
            pass

        pid = os.fork()

        # run the actual test in the child
        if pid == 0:
            # short-circuit tearDownClass(), as this will be done by the parent
            # process
            self.addCleanup(os._exit, 0)
            try:
                fn(*args, **kwargs)
            except:
                with open(exc_path, "w") as f:
                    f.write(traceback.format_exc())
                raise
        else:
            # get success/failure state from child
            os.waitpid(pid, 0)
            # propagate exception
            if os.path.exists(exc_path):
                with open(exc_path) as f:
                    self.fail(f.read())

    #
    # Internal implementation details
    #

    @classmethod
    def poll_text(klass, logpath, string, timeout=50):
        """Poll log file for a given string with a timeout.

        Timeout is given in deciseconds.
        """
        log = ""
        while timeout > 0:
            if os.path.exists(logpath):
                break
            timeout -= 1
            time.sleep(0.1)
        assert timeout > 0, "Timed out waiting for file %s to appear" % logpath

        with open(logpath) as f:
            while timeout > 0:
                line = f.readline()
                if line:
                    log += line
                    if string in line:
                        break
                    continue
                timeout -= 1
                time.sleep(0.1)

        assert (
            timeout > 0
        ), 'Timed out waiting for "%s":\n------------\n%s\n-------\n' % (string, log)

    def start_dnsmasq(self, ipv6_mode, iface):
        """Start dnsmasq.

        If ipv6_mode is None, IPv4 is set up with DHCP. If it is not None, it
        must be a valid dnsmasq mode, i. e. a combination of "ra-only",
        "slaac", "ra-stateless", and "ra-names". See dnsmasq(8).
        """
        if ipv6_mode is None:
            dhcp_range = "192.168.5.10,192.168.5.200"
        else:
            dhcp_range = "2600::10,2600::20"
            if ipv6_mode:
                dhcp_range += "," + ipv6_mode

        self.dnsmasq_log = os.path.join(self.workdir, "dnsmasq.log")
        lease_file = os.path.join(self.workdir, "dnsmasq.leases")

        p = subprocess.Popen(
            [
                "dnsmasq",
                "--keep-in-foreground",
                "--log-queries",
                "--log-facility=" + self.dnsmasq_log,
                "--conf-file=/dev/null",
                "--dhcp-leasefile=" + lease_file,
                "--bind-interfaces",
                "--interface=" + iface,
                "--except-interface=lo",
                "--enable-ra",
                "--dhcp-range=" + dhcp_range,
            ]
        )
        self.addCleanup(p.wait)
        self.addCleanup(p.terminate)

        if ipv6_mode is not None:
            self.poll_text(self.dnsmasq_log, "IPv6 router advertisement enabled")
        else:
            self.poll_text(self.dnsmasq_log, "DHCP, IP range")

    def filtered_active_connections(self) -> list:
        # Ignore the 'lo' connection, active since NM 1.42:
        # https://networkmanager.dev/blog/networkmanager-1-42/#managing-the-loopback-interface
        active_connections = [c for c in self.nmclient.get_active_connections() if c.get_id() != 'lo']
        return active_connections

    def start_nm(self, wait_iface=None, auto_connect=True, managed_devices=None):
        """Start NetworkManager and initialize client object

        If wait_iface is given, wait until NM recognizes that interface.
        Otherwise, just wait until NM has initialized (for coldplug mode).

        If auto_connect is False, set the "no-auto-default=*" option to avoid
        auto-connecting to wired devices.
        """
        # mount tmpfses over system directories, to avoid destroying the
        # production configuration, and isolating tests from each other
        if not os.path.exists("/run/NetworkManager"):
            os.mkdir("/run/NetworkManager")
        for d in [
            "/etc/NetworkManager",
            "/var/lib/NetworkManager",
            "/run/NetworkManager",
            "/run/network",
            "/etc/netplan",
        ]:
            if os.path.exists(d):
                subprocess.check_call(["mount", "-n", "-t", "tmpfs", "none", d])
                self.addCleanup(subprocess.call, ["umount", d])
        os.mkdir("/etc/NetworkManager/system-connections")

        # create local configuration; this allows us to have full control, and
        # we also need to blacklist the AP device so that NM does not tear it
        # down; we also blacklist any existing real interface to avoid
        # interfering with it, and for getting predictable results
        blacklist = ""
        if not managed_devices:
            managed_devices = [self.dev_e_client]
        for iface in os.listdir("/sys/class/net"):
            if iface == "bonding_masters":
                continue
            if iface not in managed_devices:
                with open("/sys/class/net/%s/address" % iface) as f:
                    if blacklist:
                        blacklist += ";"
                    blacklist += "mac:%s" % f.read().strip()

        conf = os.path.join(self.workdir, "NetworkManager.conf")
        extra_main = ""
        if not auto_connect:
            extra_main += "no-auto-default=*\n"

        with open(conf, "w") as f:
            f.write(
                "[main]\nplugins=keyfile\n%s\n[keyfile]\nunmanaged-devices=%s\n"
                % (extra_main, blacklist)
            )

        if NM_LOG_STDOUT:
            f_log = None
        else:
            log = os.path.join(self.workdir, "NetworkManager.log")
            f_log = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_SYNC)

        # build NM command line
        argv = ["NetworkManager", "--log-level=debug", "--debug", "--config=" + conf]
        # allow specifying extra arguments
        argv += os.environ.get("NM_TEST_DAEMON_ARGS", "").strip().split()

        p = subprocess.Popen(argv, stdout=f_log, stderr=subprocess.STDOUT)
        wait_nm_online()
        # automatically terminate process at end of test case
        self.addCleanup(p.wait)
        self.addCleanup(p.terminate)
        self.addCleanup(self.shutdown_connections)

        if NM_LOG_STDOUT:
            # let it initialize, then print a marker
            time.sleep(1)
            print("******* NM initialized *********\n\n")
        else:
            self.addCleanup(os.close, f_log)

            # this should be fast, give it 2 s to initialize
            if wait_iface:
                self.poll_text(log, "manager: (%s): new" % wait_iface, timeout=100)

        self.nmclient = NM.Client.new()
        self.assertTrue(self.nmclient.networking_get_enabled())
        self.assertTrue(self.nmclient.get_nm_running())

        # determine device objects
        for d in self.nmclient.get_devices():
            if d.props.interface == self.dev_e_client:
                self.assertEqual(d.get_device_type(), NM.DeviceType.VETH)
                self.assertEqual(d.get_driver(), "veth")
                self.assertEqual(d.get_hw_address(), self.mac_e_client)
                self.nmdev_e = d

        self.assertTrue(
            hasattr(self, "nmdev_e"), "Could not determine eth client NM device"
        )

        self.process_glib_events()

    def shutdown_connections(self):
        """Shut down all active NM connections."""

        def deactivate_cb(client, res, data):
            if not client.deactivate_connection_finish(res):
                print("WARNING: Failed to deactivate connection %s" % data.get_id(), flush=True)

        if NM_LOG_STDOUT:
            print("\n\n******* Shutting down NM connections *********")

        # remove all created connections. Ignoring the loopback interface, which
        # is actively managed since NM 1.42:
        # https://networkmanager.dev/blog/networkmanager-1-42/#managing-the-loopback-interface
        for active_conn in self.filtered_active_connections():
            self.nmclient.deactivate_connection_async(active_conn, None,
                                                      deactivate_cb, active_conn)
        try:
            # Only a single connection for the loopback interface might be left
            self.assertEventually(
                lambda: len(self.filtered_active_connections()) == 0,
                timeout=100
            )
        except AssertionError as e:
            # Log message is hidden by default, when called from an "addCleanup"
            # hook. So let's log it explicitly:
            print(f"AssertionError: get_active_connections not empty: {e}")
            print("Active connections: %s" %
                  list(map(lambda c: c.get_id(), self.nmclient.get_active_connections())))
            raise

        # verify that NM properly deconfigures the devices
        try:
            self.assert_iface_down(self.dev_e_client)
        except AssertionError as e:
            # Log message is hidden by default, when called from an "addCleanup"
            # hook. So let's log it explicitly:
            print(f"AssertionError: {e}")
            raise

    @classmethod
    def process_glib_events(klass):
        """Process pending GLib main loop events"""

        context = GLib.MainContext.default()
        while context.iteration(False):
            pass

    def assertEventually(self, condition, message=None, timeout=50):
        """Assert that condition function eventually returns True.

        timeout is in deciseconds, defaulting to 50 (5 seconds). message is
        printed on failure.
        """
        while timeout >= 0:
            self.process_glib_events()
            if condition():
                break
            if timeout % 10 == 0:  # indicate progress
                print(".", end="", flush=True)
            timeout -= 1
            time.sleep(0.1)
        else:
            self.fail(message or "timed out waiting for " + str(condition))

    def assert_iface_down(self, iface):
        """Assert that client interface is down"""

        out = subprocess.check_output(
            ["ip", "a", "show", "dev", iface], universal_newlines=True
        )
        self.assertNotIn("inet 192", out)
        self.assertNotIn("inet6 2600", out)

    def assert_iface_up(self, iface, expected_ip_a=None, unexpected_ip_a=None):
        """Assert that client interface is up"""

        out = subprocess.check_output(
            ["ip", "a", "show", "dev", iface], universal_newlines=True
        )
        self.assertIn("state UP", out)
        if expected_ip_a:
            for r in expected_ip_a:
                self.assertRegex(out, r)
        if unexpected_ip_a:
            for r in unexpected_ip_a:
                self.assertNotRegex(out, r)

    def conn_from_active_conn(self, active_conn):
        """Get NMConnection object for an NMActiveConnection object"""

        # this sometimes takes a second try, when the corresponding
        # NMConnection object is not yet available
        tries = 3
        while tries > 0:
            self.process_glib_events()
            path = active_conn.get_connection().get_path()
            for dev in active_conn.get_devices():
                for c in dev.get_available_connections():
                    if c.get_path() == path:
                        return c
            time.sleep(0.1)
            tries -= 1

        self.fail("Could not find NMConnection object for %s" % path)

    def check_low_level_config(self, iface, ipv6_mode, ip6_privacy):
        """Check actual hardware state with ip/iw after being connected"""

        # list of expected regexps in "ip a" output
        expected_ip_a = []
        unexpected_ip_a = []

        if ipv6_mode is not None:
            if ipv6_mode in ("", "slaac"):
                # has global address from our DHCP server
                expected_ip_a.append("inet6 2600::[0-9a-f]+/")
            else:
                # has address with our prefix and MAC
                expected_ip_a.append(
                    r"inet6 2600::[0-9a-f:]+/64 scope global (?:tentative )?(?:mngtmpaddr )?(?:noprefixroute )?(dynamic|\n\s*valid_lft forever preferred_lft forever)"
                )
                # has address with our prefix and random IP (Privacy
                # Extension), if requested
                priv_re = r"inet6 2600:[0-9a-f:]+/64 scope global temporary (?:tentative )?(?:mngtmpaddr )?dynamic"
                if ip6_privacy in (
                    NM.SettingIP6ConfigPrivacy.PREFER_TEMP_ADDR,
                    NM.SettingIP6ConfigPrivacy.PREFER_PUBLIC_ADDR,
                ):
                    expected_ip_a.append(priv_re)
                else:
                    # FIXME: add a negative test here
                    pass
                    # unexpected_ip_a.append(priv_re)

            # has a link-local address
            expected_ip_a.append(r"inet6 fe80::[0-9a-f:]+/64 scope link")
        else:
            expected_ip_a.append(r"inet 192.168.5.\d+/24")

        self.assert_iface_up(iface, expected_ip_a, unexpected_ip_a)

    #
    # Common test code
    #

    # libnm-glib has a lot of internal persistent state (private D-BUS
    # connections and such); as it is very brittle and hard to track down
    # all remaining references to any NM* object after a test, we rather
    # run each test in a separate subprocess
    @run_in_subprocess
    def do_test(self, ipv6_mode, ip6_privacy=None, auto_connect=True):
        """Actual test code, parameterized for the particular test case"""

        self.setup_eth(ipv6_mode)
        self.start_nm(self.dev_e_client, auto_connect=auto_connect)

        ip4_method = NM.SETTING_IP4_CONFIG_METHOD_DISABLED
        ip6_method = NM.SETTING_IP6_CONFIG_METHOD_IGNORE
        if ipv6_mode is None:
            ip4_method = NM.SETTING_IP4_CONFIG_METHOD_AUTO
        else:
            ip6_method = NM.SETTING_IP6_CONFIG_METHOD_AUTO

        if auto_connect:
            # ethernet should auto-connect quickly without an existing defined connection
            self.assertEventually(
                lambda: len(self.filtered_active_connections()) > 0,
                "timed out waiting for active connections",
                timeout=100,
            )
            active_conn = self.filtered_active_connections()[0]
        else:
            # auto-connection was disabled, set up manual connection
            partial_conn = NM.SimpleConnection.new()
            partial_conn.add_setting(NM.SettingIP4Config(method=ip4_method))
            if ip6_privacy is not None:
                partial_conn.add_setting(
                    NM.SettingIP6Config(ip6_privacy=ip6_privacy, method=ip6_method)
                )

            ml = GLib.MainLoop()
            self.cb_conn = None
            self.cancel = Gio.Cancellable()
            self.timeout_tag = 0

            def add_activate_cb(client, res, data):
                if self.timeout_tag > 0:
                    GLib.source_remove(self.timeout_tag)
                    self.timeout_tag = 0
                try:
                    self.cb_conn = self.nmclient.add_and_activate_connection_finish(res)
                except gi.repository.GLib.Error as e:
                    # Check if the error is "Operation was cancelled"
                    if e.domain != "g-io-error-quark" or e.code != 19:
                        self.fail(
                            "add_and_activate_connection failed: %s (%s, %d)"
                            % (e.message, e.domain, e.code)
                        )
                ml.quit()

            def timeout_cb():
                self.timeout_tag = -1
                self.cancel.cancel()
                ml.quit()
                return GLib.SOURCE_REMOVE

            self.nmclient.add_and_activate_connection_async(
                partial_conn, self.nmdev_e, None, self.cancel, add_activate_cb, None
            )
            self.timeout_tag = GLib.timeout_add_seconds(300, timeout_cb)
            ml.run()
            if self.timeout_tag < 0:
                self.timeout_tag = 0
                self.fail("Main loop for adding connection timed out!")
            self.assertNotEqual(self.cb_conn, None)
            active_conn = self.cb_conn
            self.cb_conn = None

        # we are usually ACTIVATING at this point; wait for completion
        self.assertEventually(
            lambda: active_conn.get_state() == NM.ActiveConnectionState.ACTIVATED,
            "timed out waiting for %s to get activated" % active_conn.get_connection(),
            timeout=150,
        )
        self.assertEqual(self.nmdev_e.get_state(), NM.DeviceState.ACTIVATED)

        conn = self.conn_from_active_conn(active_conn)
        self.assertTrue(conn.verify())

        # check NMActiveConnection object
        self.assertIn(
            active_conn.get_uuid(),
            [c.get_uuid() for c in self.filtered_active_connections()],
        )
        self.assertEqual(
            [d.get_udi() for d in active_conn.get_devices()], [self.nmdev_e.get_udi()]
        )

        # for IPv6, check privacy setting
        if ipv6_mode is not None:
            assert (
                ip6_privacy is not None
            ), "for IPv6 tests you need to specify ip6_privacy flag"
            if ip6_privacy not in (
                NM.SettingIP6ConfigPrivacy.UNKNOWN,
                NM.SettingIP6ConfigPrivacy.DISABLED,
            ):
                ip6_setting = conn.get_setting_ip6_config()
                self.assertEqual(ip6_setting.props.ip6_privacy, ip6_privacy)

        self.check_low_level_config(self.dev_e_client, ipv6_mode, ip6_privacy)
