# This file is part of Lonvia's Hiking Map
# Copyright (C) 2011 Sarah Hoffmann
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
Simple importer of OSM planet dumps and diffs into a simplified Osmosis
style postgresql database.

Diffs must be applied in correct order in order to keep database integrety.
However, diffs can be savely reapplied, i.e. it is possible to reapply an
older diff iff all diffs that follow are reapplied as well.

Diffs must not contain duplicates. Use osmosis' simplifyChange to remove
duplicates.

WARNING: This code is not yet properly tested (i.e. not at all). Use at
         your own risk.
"""

import sys
import codecs
import tempfile
import struct
import xml.parsers.expat
from optparse import OptionParser

from common import postgisconn

class DbDumper:
    tempdir = '.'
    def __init__(self,table,columns):
        self.table = table
        self.dumpfile = codecs.getwriter('utf-8')(
                tempfile.NamedTemporaryFile(prefix=table,dir=DbDumper.tempdir))
        self.counter = 0
        self.columns = columns
        self.linepattern = '\t'.join([u"%%(%s)s" % x for x in columns]) + '\n' 
        self.updatequery = "UPDATE %s SET (%s) = (%s) WHERE id = %%s" % (
                            table, ','.join(columns), ','.join(['%s' for x in columns]))
        #print "Linepattern:",self.linepattern

    def write(self, db, attrs):
        self.dumpfile.write(self.linepattern % attrs)
        self.counter += 1
        if self.counter > DbDumper.maxentries:
            self.flush(db)
            self.counter = 0

    def flush(self, db):
        cur = db.cursor()
        self.dumpfile.flush()
        self.dumpfile.seek(0)
        cur.copy_from(self.dumpfile, self.table, null='NULL', columns=self.columns)
        self.dumpfile.seek(0)
        self.dumpfile.truncate()

    def update(self, cursor, attrs):
        #print cursor.mogrify(self.updatequery, [attrs[x] for x in self.columns] + [attrs['id']])
        cursor.execute(self.updatequery, [attrs[x] for x in self.columns] + [attrs['id']])


class OSMImporter:
    columns = {
        'nodes': ('id', 'tags', 'geom' ),
        'ways': ('id', 'tags', 'nodes'),
        'relations' : ('id', 'tags'),
        'relation_members' : ( 'relation_id', 'member_id', 'member_type', 
                               'member_role', 'sequence_id' )
    }

    osm_types = ('node', 'way', 'relation')


    def __init__(self, options):
        self.options = options
        DbDumper.maxentries = options.maxentries
        DbDumper.tempdir = options.tempdir
        self.dumpers = {}
        for (tab, cols) in OSMImporter.columns.iteritems():
            self.dumpers[tab] = DbDumper(tab, cols)

    def readfile(self, filename):
        dba = ('dbname=%s user=%s password=%s' % 
               (self.options.database, self.options.username, self.options.password))
        self.db = postgisconn.connect(dba)
        self.cursor = self.db.cursor()

        parser = xml.parsers.expat.ParserCreate()
        parser.StartElementHandler = self.parse_start_element
        parser.EndElementHandler = self.parse_end_element
        self.handler = self.handle_initial_state

        if filename == '-':
            fid = sys.stdin
        else:
            fid = open(filename,'r')
        parser.ParseFile(fid)
        fid.close()
        for tab in self.dumpers.itervalues():
            tab.flush(self.db)
        self.db.commit()

    def parse_start_element(self, name, attrs):
        self.handler(True, name, attrs)

    def parse_end_element(self, name):
        self.handler(False, name, {})

    def handle_initial_state(self, start, name, attrs):
        if start and name in ('osm','osmChange'):
            if 'version' not in attrs:
                raise Exception('Not a valid OSM file. Version information missing.')
            if attrs['version'] != '0.6':
                raise Exception("OSM file is of version %s. Can only handle version 0.6 files." 
                                  % attrs['version']) 

            self.handler = self.handle_top_level
            self.intag = None
            self.filetype = name
            self.action = None
            if name == 'osmChange':
                for tab in OSMImporter.osm_types:
                    tablename = '%s_changeset' % tab
                    self.dumpers[tablename] = DbDumper(tablename, ('id', 'action'))
        else:
            raise Exception("Not an OSM file.")

    def handle_top_level(self, start, name, attrs):
        #print "Top level:",name,attrs,start
        if start and name in OSMImporter.osm_types:
            if not 'id' in attrs:
                raise Exception('%s has no id.' % name)
            self.current = {}
            self.current['type'] = name
            self.current['id'] = attrs['id']
            self.current['tags'] = {}
            self.memberseq = 1
            if self.filetype == 'osmChange':
                if self.action is None:
                    raise Exception('Missing change type for %s %s.' % (name, attrs['id']))
                self.prepare_object()
            if name == 'way':
                self.current['nodes'] = []
            elif name == 'node':
                if 'lat' not in attrs or 'lon' not in attrs:
                    raise Exception('Node %s is missing coordinates.' % attrs['id'])
                # WKB representation
                # PostGIS extension that includes a SRID, see postgis/doc/ZMSGeoms.txt
                self.current['geom'] = struct.pack("=biidd", 1, 0x20000001, 4326, 
                                        float(attrs['lon']), float(attrs['lat'])).encode('hex')
            self.handler = self.handle_object
        elif start and name == 'bound':
            self.handler = self.handle_bound
        elif start and name == 'changeset':
            self.handler = self.handle_changeset
        elif name in ('modify', 'create', 'delete'):
            if start:
                if self.action is None:
                    self.action = name
                else:
                    raise Exception('Nested change actions.')
            else:
                if self.action == name:
                    self.action = None
                else:
                    raise Exception('Unexpected change action.')
                    
        elif not start and name == self.filetype:
            self.handler = self.handle_eof
        else:
            raise Exception('Unexpected element: %s' % name)

    def handle_bound(self, start, name, attrs):
        if not start and name == 'bound':
            self.handler = self.handle_top_level
        else:
            raise Exception("Unexpected data in bound description.")

    def handle_changeset(self, start, name, attrs):
        if not start and name == 'changeset':
            self.handler = self.handle_top_level

    def handle_object(self, start, name, attrs):
        #print "Object level:",name,attrs,start
        if start:
            if self.intag:
                raise Exception("Closing element %s missing."% (self.intag))
            if name == 'tag':
                if 'k' not in attrs or 'v' not in attrs:
                    raise Exception("tag element of invalid format") 
                self.current['tags'][attrs['k']] = attrs['v']
            elif name == 'nd' and 'nodes'in self.current:
                if not 'ref' in attrs or not attrs['ref'].isdigit(): 
                    print self.current
                    raise Exception("Unexcpected nd element")
                self.current['nodes'].append(attrs['ref'])
            elif name == 'member':
                 self.write_relation_member(attrs)
            else:
                raise Exception("Unexexpected element %s."% name)
                
            self.intag = name
        else:
            if self.intag:
                if name == self.intag:
                    self.intag = None
                else:
                    raise Exception("Closing element %s missing."% (self.intag))
            elif name == self.current['type']:
                self.write_object()
                self.handler = self.handle_top_level
            else:
                raise Exception("Spurious final tag %s" % name)

    def handle_eof(self, start, name, attrs):
        raise Exception("Data after end of file.")

    sqltrans = { ord(u'"'): u'\\\\"',
                     ord(u'\r') : u' ', ord(u'\b'): None, ord(u'\f') : None, 
                     ord(u'\n') : u' ',
                     ord(u'\t') : u' ',
                     ord(u'\\') : u'\\\\\\\\'
               }

    def write_relation_member(self, attrs):
        if self.current['type'] != 'relation':
            raise Exception("member element in a %s" % self.current['type'])

        if not attrs.get('type', None) in OSMImporter.osm_types:
            raise Exception("Missing or unknown type attribute in member element.")
        if not 'ref' in attrs or not attrs['ref'].isdigit():
            raise Exception("Missing attribute ref in member element.") 
        if not 'role' in attrs:
            raise Exception("Missing attribute role in member element.") 
        self.dumpers['relation_members'].write(self.db,
                {'relation_id' : self.current['id'],
                 'member_id': attrs['ref'],
                 'member_type' : attrs['type'][0].upper(),
                 'member_role' : '"%s"' % attrs['role'].translate(OSMImporter.sqltrans),
                 'sequence_id' : self.memberseq})
        self.memberseq += 1 

    def write_object(self):
        if self.action is not None:
            self.dumpers[self.current['type'] + '_changeset'].write(
                    self.db, { 'id' : self.current['id'], 'action' : self.action[0].upper() })
        if self.action == 'delete':
            self.cursor.execute("DELETE FROM %ss WHERE id = %%s" % self.current['type'],
                                  (self.current['id'],))
        else:
            if self.action is not None:
                # check if we should modify
                self.cursor.execute("SELECT id FROM %ss WHERE id=%%s" 
                                            % self.current['type'], (self.current['id'],))
                if self.cursor.fetchone() is not None:
                    if 'nodes' in self.current:
                        self.current['nodes'] = [long(x) for x in self.current['nodes']]
                    self.dumpers[self.current['type']+'s'].update(self.cursor, self.current)
                    return

            # fix the tags string
            taglist = ['"%s"=>"%s"' % (k.translate(OSMImporter.sqltrans), 
                                       v.translate(OSMImporter.sqltrans)) 
                       for  (k,v) in self.current['tags'].iteritems()]
            self.current['tags'] = u','.join(taglist)
            if 'nodes' in self.current:
                self.current['nodes'] = u'{%s}' % ( 
                                       ','.join([x for x in self.current['nodes']]))
            self.dumpers[self.current['type']+'s'].write(self.db, self.current)

    def prepare_object(self):
        if self.current['type'] == 'relation':
            self.cursor.execute("""DELETE FROM relation_members 
                                  WHERE relation_id = %s""", (self.current['id'],))


if __name__ == '__main__':

    # fun with command line options
    parser = OptionParser(description=__doc__,
                          usage='%prog [options] <osm file>')
    parser.add_option('-d', action='store', dest='database', default='osmosis',
                       help='name of database')
    parser.add_option('-u', action='store', dest='username', default='osm',
                       help='database user')
    parser.add_option('-p', action='store', dest='password', default='',
                       help='password for database')
    parser.add_option('-t', action="store", dest='tempdir', default=None,
                       help="directory to use for temporary files")
    parser.add_option('-m', action='store', dest='maxentries', default=100000000,
                       help='Maximum number of objects to cache before writing to the database.')

    (options, args) = parser.parse_args()

    if len(args) == 0:
        OSMImporter(options).readfile('-')
    if len(args) == 1:
        OSMImporter(options).readfile(args[0])
    else:
        parser.print_help()
