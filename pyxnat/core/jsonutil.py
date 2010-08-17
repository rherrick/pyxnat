import csv
import copy
from fnmatch import fnmatch
from StringIO import StringIO

from ..externals import simplejson as json

# jdata is a list of dicts

def join_tables(join_column, jdata, *jtables):
    index1 = {}
    index2 = {}

    indexes = []

    for jtable in [jdata]+list(jtables):
        if isinstance(jtable, dict):
            jtable = [jtable]
        index = {}
        [index.setdefault(entry[join_column], entry) for entry in jtable]
        indexes.append(index)

    merged_jdata = []
    for join_id in indexes[0].keys():
        for index in indexes[1:]:
            indexes[0][join_id].update(index[join_id])
            merged_jdata.append(indexes[0][join_id])

    return merged_jdata

def get_column(jdata, col, val_pattern='*'):
    if isinstance(jdata, dict):
        jdata = [jdata]

    if val_pattern == '*':
        return [entry[col] for entry in jdata if entry.has_key(col)]
    else:
        return [entry[col] for entry in jdata if fnmatch(entry.get(col), val_pattern)]

def get_where(jdata, *args, **kwargs):
    if isinstance(jdata, dict):
        jdata = [jdata]

    match = []

    for entry in jdata:
        match_args = all([arg in entry.keys() or arg in entry.values() for arg in args])
        match_kwargs = all([entry[key] == kwargs[key] for key in kwargs.keys()])

        if match_args and match_kwargs:
            match.append(entry)

    return match

def get_headers(jdata):
    if isinstance(jdata, dict):
        jdata = [jdata]
    return [] if jdata == [] else jdata[0].keys()

def get_selection(jdata, columns):
    if isinstance(jdata, dict):
        jdata = [jdata]

    sub_table = copy.deepcopy(jdata)

    rmcols = set(get_headers(jdata)).difference(columns)

    for entry in sub_table:
        for col in rmcols:
            if entry.has_key(col):
                del entry[col]

    return sub_table

def csv_to_json(csv_str):
    csv_reader = csv.reader(StringIO(csv_str), delimiter=',', quotechar='"')
    headers = csv_reader.next()

    return [dict(zip(headers, entry)) for entry in csv_reader]


class JsonTable(object):
    def __init__(self, jdata, order_by=[]):
        self.data = jdata
        self.order_by = order_by

    def __repr__(self):
        if len(self.data) == 0:
            return '[]'
        elif len(self.data) == 1:
            return str(self.data[0])
        
        return ('[%s\n .\n .\n . \n%s]\n\n'
                '------------\n'
                '%s rows\n'
                '%s columns\n'
                '%s characters') %(str(self.data[0]), 
                                   str(self.data[-1]),
                                   len(self), 
                                   len(self.headers()),
                                   len(self.dumps_csv()) 
                                   )

    def __str__(self):
        return self.dumps_csv()

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)

    def __getitem__(self, name):
        if isinstance(name, (str, unicode)):
            return self.get(name)
        elif isinstance(name, int):
            return self.__class__([self.data[name]], self.order_by)
        elif isinstance(name, list):
            return self.select(name)

    def __getslice__(self, i, j):
        return self.__class__(self.data[i:j], self.order_by)

    def join(self, join_column, *jtables):
        return self.__class__(join_tables(join_column, self.data, 
                                          *[jtable.data for jtable in jtables]),
                              self.order_by)

    def headers(self):
        return get_headers(self.data)

    def get(self, col, val_pattern='*', always_list=False):
        res = get_column(self.data, col, val_pattern)
        if always_list:
            return res
        if len(self.data) == 1:
            return res[0]
        return res

    def where(self, *args, **kwargs):
        return self.__class__(get_where(self.data, *args, **kwargs), self.order_by)

    def select(self, columns):
        return self.__class__(get_selection(self.data, columns), self.order_by)

    def dump_csv(self, dest, delimiter=','):
        fd = open(dest, 'w')
        fd.write(self.dumps_csv(delimiter))
        fd.close()

    def dumps_csv(self, delimiter=','):
        str_buffer = StringIO()
        csv_writer = csv.writer(str_buffer, delimiter=delimiter, 
                                quotechar='"', quoting=csv.QUOTE_MINIMAL)

        for entry in self.as_list():
            csv_writer.writerow(entry)

        return str_buffer.getvalue()

    def dump_json(self, dest):
        fd = open(dest, 'w')
        fd.write(self.dumps_json())
        fd.close()

    def dumps_json(self):
        return json.dumps(self.data)

    def as_list(self):
        table = [[]]

        for header in self.order_by:
            if header in self.headers():
                table[0].append(header)
        for header in self.headers():
            if header not in self.order_by:
                table[0].append(header)

        for entry in self.data:
            row = []
            for header in self.order_by:
                if entry.has_key(header):
                    row.append(entry.get(header))
            for header in self.headers():
                if header not in self.order_by:
                    row.append(entry.get(header))
            table.append(row)
        
        return table
