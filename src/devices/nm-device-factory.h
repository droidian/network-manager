/* -*- Mode: C; tab-width: 4; indent-tabs-mode: t; c-basic-offset: 4 -*- */
/* NetworkManager -- Network link manager
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 *
 * Copyright (C) 2007 - 2014 Red Hat, Inc.
 */

#ifndef __NETWORKMANAGER_DEVICE_FACTORY_H__
#define __NETWORKMANAGER_DEVICE_FACTORY_H__

#include <glib.h>
#include <glib-object.h>

#include "nm-dbus-interface.h"
#include "nm-device.h"

/* WARNING: this file is private API between NetworkManager and its internal
 * device plugins.  Its API can change at any time and is not guaranteed to be
 * stable.  NM and device plugins are distributed together and this API is
 * not meant to enable third-party plugins.
 */

typedef struct _NMDeviceFactory NMDeviceFactory;

/**
 * nm_device_factory_create:
 * @error: an error if creation of the factory failed, or %NULL
 *
 * Creates a #GObject that implements the #NMDeviceFactory interface. This
 * function must not emit any signals or perform any actions that would cause
 * devices or components to be created immediately.  Instead these should be
 * deferred to the "start" interface method.
 *
 * Returns: the #GObject implementing #NMDeviceFactory or %NULL
 */
NMDeviceFactory *nm_device_factory_create (GError **error);

/* Should match nm_device_factory_create() */
typedef NMDeviceFactory * (*NMDeviceFactoryCreateFunc) (GError **error);

/********************************************************************/

#define NM_TYPE_DEVICE_FACTORY               (nm_device_factory_get_type ())
#define NM_DEVICE_FACTORY(obj)               (G_TYPE_CHECK_INSTANCE_CAST ((obj), NM_TYPE_DEVICE_FACTORY, NMDeviceFactory))
#define NM_IS_DEVICE_FACTORY(obj)            (G_TYPE_CHECK_INSTANCE_TYPE ((obj), NM_TYPE_DEVICE_FACTORY))
#define NM_DEVICE_FACTORY_GET_INTERFACE(obj) (G_TYPE_INSTANCE_GET_INTERFACE ((obj), NM_TYPE_DEVICE_FACTORY, NMDeviceFactory))

/* signals */
#define NM_DEVICE_FACTORY_COMPONENT_ADDED "component-added"
#define NM_DEVICE_FACTORY_DEVICE_ADDED    "device-added"

struct _NMDeviceFactory {
	GTypeInterface g_iface;

	/**
	 * get_device_type:
	 * @factory: the #NMDeviceFactory
	 *
	 * This function MUST be implemented.
	 *
	 * Returns: the #NMDeviceType that this plugin creates
	 */
	NMDeviceType (*get_device_type) (NMDeviceFactory *factory);

	/**
	 * start:
	 * @factory: the #NMDeviceFactory
	 *
	 * Start the factory and discover any existing devices that the factory
	 * can manage.
	 */
	void (*start)                   (NMDeviceFactory *factory);

	/**
	 * new_link:
	 * @factory: the #NMDeviceFactory
	 * @link: the new link
	 * @error: error if the link could be claimed but an error occurred
	 *
	 * The NetworkManager core was notified of a new link which the plugin
	 * may want to claim and create a #NMDevice subclass for.  If the link
	 * represents a device the factory is capable of claiming, but the device
	 * could not be created, %NULL should be returned and @error should be set.
	 * %NULL should always be returned and @error should never be set if the
	 * factory cannot create devices for the type which @link represents.
	 *
	 * Returns: the #NMDevice if the link was claimed and created, %NULL if not
	 */
	NMDevice * (*new_link)        (NMDeviceFactory *factory,
	                               NMPlatformLink *plink,
	                               GError **error);

	/**
	 * create_virtual_device_for_connection:
	 * @factory: the #NMDeviceFactory
	 * @connection: the #NMConnection
	 * @error: a @GError in case of failure
	 *
	 * Virtual device types (such as team, bond, bridge) may need to be created.
	 * This function tries to create a device based on the given @connection.
	 *
	 * Returns: the newly created #NMDevice. If the factory does not support the
	 * connection type, it should return %NULL and leave @error unset. On error
	 * it should set @error and return %NULL.
	 */
	NMDevice * (*create_virtual_device_for_connection) (NMDeviceFactory *factory,
	                                                    NMConnection *connection,
	                                                    NMDevice *parent,
	                                                    GError **error);


	/* Signals */

	/**
	 * device_added:
	 * @factory: the #NMDeviceFactory
	 * @device: the new #NMDevice subclass
	 *
	 * The factory emits this signal if it finds a new device by itself.
	 */
	void       (*device_added)    (NMDeviceFactory *factory, NMDevice *device);

	/**
	 * component_added:
	 * @factory: the #NMDeviceFactory
	 * @component: a new component which existing devices may wish to claim
	 *
	 * The factory emits this signal when it finds a new component.  For example,
	 * the WWAN factory may indicate that a new modem is available, which an
	 * existing Bluetooth device may wish to claim.  If no device claims the
	 * component, the plugin is allowed to create a new #NMDevice instance for
	 * that component and emit the "device-added" signal.
	 *
	 * Returns: %TRUE if the component was claimed by a device, %FALSE if not
	 */
	gboolean   (*component_added) (NMDeviceFactory *factory, GObject *component);
};

GType      nm_device_factory_get_type    (void);

NMDeviceType nm_device_factory_get_device_type (NMDeviceFactory *factory);

void       nm_device_factory_start       (NMDeviceFactory *factory);

NMDevice * nm_device_factory_new_link    (NMDeviceFactory *factory,
                                          NMPlatformLink *plink,
                                          GError **error);

NMDevice * nm_device_factory_create_virtual_device_for_connection (NMDeviceFactory *factory,
                                                                   NMConnection *connection,
                                                                   NMDevice *parent,
                                                                   GError **error);

/* For use by implementations */
gboolean   nm_device_factory_emit_component_added (NMDeviceFactory *factory,
                                                   GObject *component);

/**************************************************************************
 * INTERNAL DEVICE FACTORY FUNCTIONS - devices provided by plugins should
 * not use these functions.
 **************************************************************************/

#define DEFINE_DEVICE_FACTORY_INTERNAL(upper, mixed, lower, dfi_code) \
	DEFINE_DEVICE_FACTORY_INTERNAL_WITH_DEVTYPE(upper, mixed, lower, upper, dfi_code)

#define DEFINE_DEVICE_FACTORY_INTERNAL_WITH_DEVTYPE(upper, mixed, lower, devtype, dfi_code) \
	typedef GObject NM##mixed##Factory; \
	typedef GObjectClass NM##mixed##FactoryClass; \
 \
	static GType nm_##lower##_factory_get_type (void); \
	static void device_factory_interface_init (NMDeviceFactory *factory_iface); \
 \
	G_DEFINE_TYPE_EXTENDED (NM##mixed##Factory, nm_##lower##_factory, G_TYPE_OBJECT, 0, \
	                        G_IMPLEMENT_INTERFACE (NM_TYPE_DEVICE_FACTORY, device_factory_interface_init) \
	                        _nm_device_factory_internal_register_type (g_define_type_id);) \
 \
	/* Use a module constructor to register the factory's GType at load \
	 * time, which then calls _nm_device_factory_internal_register_type() \
	 * to register the factory's GType with the Manager. \
	 */ \
	static void __attribute__((constructor)) \
	register_device_factory_internal_##lower (void) \
	{ \
		g_type_init (); \
		g_type_ensure (NM_TYPE_##upper##_FACTORY); \
	} \
 \
	static NMDeviceType \
	get_device_type (NMDeviceFactory *factory) \
	{ \
		return NM_DEVICE_TYPE_##devtype; \
	} \
 \
	static void \
	device_factory_interface_init (NMDeviceFactory *factory_iface) \
	{ \
		factory_iface->get_device_type = get_device_type; \
		dfi_code \
	} \
 \
	static void \
	nm_##lower##_factory_init (NM##mixed##Factory *self) \
	{ \
	} \
 \
	static void \
	nm_##lower##_factory_class_init (NM##mixed##FactoryClass *lower##_class) \
	{ \
	}

void _nm_device_factory_internal_register_type (GType factory_type);
const GSList *nm_device_factory_get_internal_factory_types (void);

#endif /* __NETWORKMANAGER_DEVICE_FACTORY_H__ */
