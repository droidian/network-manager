#!/usr/bin/env python3
# Test NetworkManager on simulated network devices
# For an interactive shell, run "nm-eth.py ColdplugEthernet.shell", see below.

__author__ = "Martin Pitt <martin.pitt@ubuntu.com>"
__copyright__ = "(C) 2013-2025 Canonical Ltd."
__license__ = "GPL v2 or later"

import os
import os.path
import subprocess
import sys
import unittest

import gi

import base

gi.require_version("NM", "1.0")
from gi.repository import NM


class ColdplugEthernet(base.NetworkTestBase):
    """Ethernet: In these tests NM starts after setting up the router"""

    # not run by default; run "nm-eth.py ColdplugEthernet.shell" to get this
    @base.run_in_subprocess
    def shell(self):
        """Start router and NM, then run a shell (for debugging)"""

        self.setup_eth(None)
        self.start_nm(self.dev_e_client)
        print(
            """

client interface: %s, router interface: %s

You can now run commands like "nmcli dev".
Logs are in '%s'. When done, exit the shell.

"""
            % (self.dev_e_client, self.dev_e_ap, self.workdir)
        )
        subprocess.call(["bash", "-i"])

    def test_auto_ip4(self):
        """ethernet: auto-connection, IPv4"""

        self.do_test(None, auto_connect=True)

    def test_auto_ip6_raonly_no_pe(self):
        """ethernet: auto-connection, IPv6 with only RA, PE disabled"""

        self.do_test(
            "ra-only",
            auto_connect=True,
            ip6_privacy=NM.SettingIP6ConfigPrivacy.DISABLED,
        )

    def test_auto_ip6_dhcp(self):
        """ethernet: auto-connection, IPv6 with DHCP"""

        self.do_test(
            "", auto_connect=True, ip6_privacy=NM.SettingIP6ConfigPrivacy.UNKNOWN
        )

    def test_manual_ip4(self):
        """ethernet: manual connection, IPv4"""

        self.do_test(None, auto_connect=False)

    def test_manual_ip6_raonly_tmpaddr(self):
        """ethernet: manual connection, IPv6 with only RA, preferring temp address"""

        self.do_test(
            "ra-only",
            auto_connect=False,
            ip6_privacy=NM.SettingIP6ConfigPrivacy.PREFER_TEMP_ADDR,
        )

    def test_manual_ip6_raonly_pubaddr(self):
        """ethernet: manual connection, IPv6 with only RA, preferring public address"""

        self.do_test(
            "ra-only",
            auto_connect=False,
            ip6_privacy=NM.SettingIP6ConfigPrivacy.PREFER_PUBLIC_ADDR,
        )


class HotplugEthernet(base.NetworkTestBase):
    """In these tests routers are are created while NM is already running"""

    @base.run_in_subprocess
    def test_auto_detect_eth(self):
        """new eth router is being detected automatically within 30s"""

        self.start_nm()
        self.setup_eth(None)
        self.assertEventually(
            lambda: len(self.filtered_active_connections()) > 0, timeout=300
        )
        active_conn = self.filtered_active_connections()[0]

        self.assertEventually(
            lambda: active_conn.get_state() == NM.ActiveConnectionState.ACTIVATED,
            "timed out waiting for %s to get activated" % active_conn.get_connection(),
            timeout=80,
        )
        self.assertEqual(self.nmdev_e.get_state(), NM.DeviceState.ACTIVATED)

        conn = self.conn_from_active_conn(active_conn)
        self.assertTrue(conn.verify())


def setUpModule():
    base.set_up_module()

def tearDownModule():
    base.tear_down_module()


if __name__ == "__main__":
    # avoid unintelligible error messages when not being root
    if os.getuid() != 0:
        raise SystemExit("This integration test suite needs to be run as root")

    # write to stdout, not stderr
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner)
