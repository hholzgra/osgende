# This file is part of Osgende
# Copyright (C) 2018 Sarah Hoffmann
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
Provide special compare classes for complex database values.
"""

from geoalchemy2.elements import _SpatialElement as GeoElement
from geoalchemy2.shape import to_shape
import shapely.geometry as sgeom
from nose.tools import *

class DBCompareValue(object):
    """ Generic DB value comparator. Inherit from this class for more
        specific comparators and implement the compare() function.
    """

    # Cache locations for nodes here.
    nodestore = {}

    @classmethod
    def compare(cls, a, b):
        if isinstance(b, DBCompareValue):
            return b.compare(a)

        if type(a) != type(b):
            return False

        if isinstance(a, str):
            return a == b

        if isinstance(a, dict) and isinstance(b, dict):
            if len(a) != len(b):
                return False

            for k,v in a.items():
                if k not in b or not cls.compare(v, b[k]):
                    return False

        try:
            for suba, subb in zip(iter(a), iter(b)):
                if not cls.compare(suba, subb):
                    return False
        except TypeError:
            pass

        return a == b


class Line(DBCompareValue):
    """ Compare with a GeoAlchemy or Shapely LineString. """

    def __init__(self, *args):
        self.points = []

        for a in args:
            if isinstance(a, int):
                assert_in(a, DBCompareValue.nodestore)
                a = DBCompareValue.nodestore[a]

            assert_equal(2, len(a), "not a point: " + str(a))
            self.points.append(a)

    def compare(self, o):
        if isinstance(o, GeoElement):
            o = to_shape(o)

        if not isinstance(o, sgeom.LineString):
            return False

        if len(self.points) != len(o.coords):
            return False

        for a, e in zip(self.points, o.coords):
            if abs(a[0] - e[0]) > 0.00000001:
                return False
            if abs(a[1] - e[1]) > 0.00000001:
                return False

        assert_true(o.is_valid)

        return True

