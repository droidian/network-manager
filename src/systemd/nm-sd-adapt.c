/* -*- Mode: C; tab-width: 4; indent-tabs-mode: t; c-basic-offset: 4 -*- */
/* This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2, or (at your option)
 * any later version.
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
 * Copyright (C) 2014 Red Hat, Inc.
 */

#include "nm-default.h"

#include "nm-sd-adapt.h"

#include <unistd.h>
#include <errno.h>

#include "sd-event.h"
#include "fd-util.h"
#include "time-util.h"

struct sd_event_source {
	guint refcount;
	guint id;
	gpointer user_data;

	GIOChannel *channel;

	union {
		struct {
			sd_event_io_handler_t cb;
		} io;
		struct {
			sd_event_time_handler_t cb;
			uint64_t usec;
		} time;
	};
};

static struct sd_event_source *
source_new (void)
{
	struct sd_event_source *source;

	source = g_slice_new0 (struct sd_event_source);
	source->refcount = 1;
	return source;
}

int
sd_event_source_set_priority (sd_event_source *s, int64_t priority)
{
	return 0;
}

sd_event_source*
sd_event_source_unref (sd_event_source *s)
{

	if (!s)
		return NULL;

	g_return_val_if_fail (s->refcount, NULL);

	s->refcount--;
	if (s->refcount == 0) {
		if (s->id)
			g_source_remove (s->id);
		if (s->channel) {
			/* Don't shut down the channel since systemd will soon close
			 * the file descriptor itself, which would cause -EBADF.
			 */
			g_io_channel_unref (s->channel);
		}
		g_slice_free (struct sd_event_source, s);
	}
	return NULL;
}

int
sd_event_source_set_description(sd_event_source *s, const char *description)
{
	if (!s)
		return -EINVAL;

	g_source_set_name_by_id (s->id, description);
	return 0;
}

static gboolean
io_ready (GIOChannel *channel, GIOCondition condition, struct sd_event_source *source)
{
	int r, revents = 0;
	gboolean result;

	if (condition & G_IO_IN)
		revents |= EPOLLIN;
	if (condition & G_IO_OUT)
		revents |= EPOLLOUT;
	if (condition & G_IO_PRI)
		revents |= EPOLLPRI;
	if (condition & G_IO_ERR)
		revents |= EPOLLERR;
	if (condition & G_IO_HUP)
		revents |= EPOLLHUP;

	source->refcount++;

	r = source->io.cb (source, g_io_channel_unix_get_fd (channel), revents, source->user_data);
	if (r < 0 || source->refcount <= 1) {
		source->id = 0;
		result = G_SOURCE_REMOVE;
	} else
		result = G_SOURCE_CONTINUE;

	sd_event_source_unref (source);

	return result;
}

int
sd_event_add_io (sd_event *e, sd_event_source **s, int fd, uint32_t events, sd_event_io_handler_t callback, void *userdata)
{
	struct sd_event_source *source;
	GIOChannel *channel;
	GIOCondition condition = 0;

	/* systemd supports floating sd_event_source by omitting the @s argument.
	 * We don't have such users and don't implement floating references. */
	g_return_val_if_fail (s, -EINVAL);

	channel = g_io_channel_unix_new (fd);
	if (!channel)
		return -EINVAL;

	source = source_new ();
	source->io.cb = callback;
	source->user_data = userdata;
	source->channel = channel;

	if (events & EPOLLIN)
		condition |= G_IO_IN;
	if (events & EPOLLOUT)
		condition |= G_IO_OUT;
	if (events & EPOLLPRI)
		condition |= G_IO_PRI;
	if (events & EPOLLERR)
		condition |= G_IO_ERR;
	if (events & EPOLLHUP)
		condition |= G_IO_HUP;

	g_io_channel_set_encoding (source->channel, NULL, NULL);
	g_io_channel_set_buffered (source->channel, FALSE);
	source->id = g_io_add_watch (source->channel, condition, (GIOFunc) io_ready, source);

	*s = source;
	return 0;
}

static gboolean
time_ready (struct sd_event_source *source)
{
	source->refcount++;

	source->time.cb (source, source->time.usec, source->user_data);
	source->id = 0;

	sd_event_source_unref (source);

	return G_SOURCE_REMOVE;
}

int
sd_event_add_time(sd_event *e, sd_event_source **s, clockid_t clock, uint64_t usec, uint64_t accuracy, sd_event_time_handler_t callback, void *userdata)
{
	struct sd_event_source *source;
	uint64_t n = now (clock);

	/* systemd supports floating sd_event_source by omitting the @s argument.
	 * We don't have such users and don't implement floating references. */
	g_return_val_if_fail (s, -EINVAL);

	source = source_new ();
	source->time.cb = callback;
	source->user_data = userdata;
	source->time.usec = usec;

	if (usec > 1000)
		usec = n < usec - 1000 ? usec - n : 1000;
	source->id = g_timeout_add (usec / 1000, (GSourceFunc) time_ready, source);

	*s = source;
	return 0;
}

/* sd_event is basically a GMainContext; but since we only
 * ever use the default context, nothing to do here.
 */

int
sd_event_default (sd_event **e)
{
	*e = GUINT_TO_POINTER (1);
	return 0;
}

sd_event*
sd_event_ref (sd_event *e)
{
	return e;
}

sd_event*
sd_event_unref (sd_event *e)
{
	return NULL;
}

int
sd_event_now (sd_event *e, clockid_t clock, uint64_t *usec)
{
	*usec = now (clock);
	return 0;
}

int asynchronous_close(int fd) {
	safe_close(fd);
	return -1;
}

