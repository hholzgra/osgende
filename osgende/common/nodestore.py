# This file is part of Osgende
# Copyright (C) 2012 Sarah Hoffmann
#
# This is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
"""
File-backed storage for node geometries.
"""

import logging

from osmium import index, osm
from binascii import hexlify
from struct import pack
from collections import namedtuple
from osmium.geom import lonlat_to_mercator, Coordinates

log = logging.getLogger(__name__)

class NodeStorePoint(namedtuple('NodeStorePoint', ['x', 'y'])):

    def wkb(self):
        # PostGIS extension that includes a SRID, see postgis/doc/ZMSGeoms.txt
        return hexlify(pack("=biidd", 1, 0x20000001, 4326,
                                   self.x, self.y)).decode()

    def to_mercator(self):
        c = lonlat_to_mercator(Coordinates(self.x, self.y))
        return NodeStorePoint(c.x, c.y)

class NodeStore(object):
    """Provides a map like persistent storage for node geometries.

       This implementation relies on a osmium location index.
    """

    def __init__(self, filename):
        self.mapfile = index.create_map("dense_file_array," + filename)

    def __del__(self):
        self.close()

    def __getitem__(self, nodeid):
        loc = self.mapfile.get(nodeid)
        return NodeStorePoint(loc.lon, loc.lat)

    def __setitem__(self, nodeid, value):
        self.mapfile.set(nodeid, osm.Location(value.x, value.y))

    def __delitem__(self, nodeid):
        self.mapfile.set(nodeid, osm.Location())

    def set_from_node(self, node):
        self.mapfile.set(node.id, node.location)

    def close(self):
        if hasattr(self, 'mapfile'):
            log.info("Used memory by index: %d" % self.mapfile.used_memory())
            del self.mapfile


if __name__ == '__main__':
    print("Creating store...")
    store = NodeStore('test.store')

    print("Filling store...")
    for i in range(25500,26000):
        store[i] = NodeStorePoint(1,i/1000.0)

    store.close()
    del store

    print("Reloading store...")
    store = NodeStore('test.store')

    print("Checking store...")
    for i in range(25500,26000):
        assert store[i].y == i/1000.0

    try:
        x = store[1000]
    except KeyError:
        print("Yeah!")

    try:
        x = store[0]
    except KeyError:
        print("Yeah!")

    print("Filling store...")
    for i in range(100055500,100056000):
        store[i] = NodeStorePoint(i/10000000.0,1)

    try:
        x = store[26001]
        print("Unexpected node location:", x)
    except KeyError:
        print("Yeah!")

    store.close()
    del store

    print("Reloading store...")
    store = NodeStore('test.store')

    print("Checking store...")
    for i in range(100055500,100056000):
        assert store[i].x == i/10000000.0

    print("Checking store...")
    for i in range(25500,26000):
        assert store[i].y == i/1000.0


    store.close()

