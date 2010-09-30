import os
import re
import time
import tempfile
import email
import getpass
import hashlib
import sqlite3

from ..externals import httplib2
from ..externals import simplejson as json

from .select import Select
from .cache import CacheManager, SQLCache
from .help import Inspector
from .manage import GlobalManager
from .connection import ConnectionManager
from .uriutil import join_uri
from .jsonutil import csv_to_json
from .errors import is_xnat_error, raise_exception, ResourceConcurrentAccessError
from . import sqlutil

DEBUG = False

# main entry point
class Interface(object):
    """ Main entry point to access a XNAT server.

        >>> central = Interface(server='http://central.xnat.org:8080',
                                user='login',
                                password='pwd',
                                cachedir='/tmp'
                                )

        Attributes
        ----------
        _mode: online | offline
            Online or offline mode
        _memtimeout: float
            Lifespan of in-memory cache
    """

    def __init__(self, server_or_config=None, user=None, password=None, 
                                              cachedir=tempfile.gettempdir()):
        """ 
            Parameters
            ----------
            server_or_config: string | None
                The server full URL (including port and XNAT instance name if necessary)
                e.g. http://central.xnat.org, http://localhost:8080/xnat_db
                Or a path to an existing config file. In that case the other
                parameters (user etc..) are ignored if given.
                If None the user will be prompted for it.
            user: string | None
                A valid login registered through the XNAT web interface.
                If None the user will be prompted for it.
            password: string | None
                The user's password.
                If None the user will be prompted for it.
            cachedir: string
                Path of the cache directory (for all queries and downloaded files)
                If no path is provided, a platform dependent temp dir is used.
        """

        if server_or_config is not None:
            server = server_or_config
            if os.path.exists(server_or_config):
                fp = open(server_or_config, 'rb')
                config = json.load(fp)
                fp.close()
                server = config['server']
                user = config['user']
                password = config['password']
                cachedir = config['cachedir']
        else:
            server = raw_input('Server: ')

        self._server = server
        self._interactive = user is None or password is None

        if user is None:
            user = raw_input('User: ')

        if password is None:
            password = getpass.getpass()

        self._user = user
        self._pwd = password

        self._cachedir = os.path.join(cachedir, 
                                      '%s@%s'%(self._user,
                                               self._server.split('//')[1].replace('/', '.')
                                               ))
        
        self._callback = None

        self._memcache = {}
        self._memtimeout = 1.0
        self._mode = 'online'

        self._last_memtimeout = 1.0
        self._last_mode = 'online'

        self._jsession = 'authentication_by_credentials'
        self._connect()
        self._setup_sqlites()

        self.inspect = Inspector(self)
        self.select = Select(self)
        self.cache = CacheManager(self)
        self.connection = ConnectionManager(self)
        self.manage = GlobalManager(self)

        if self._interactive:
            self._jsession = self._exec('/REST/JSESSION')
            if is_xnat_error(self._jsession):
                raise_exception(self._jsession)

    def learn(self, project=None):
        self.cache.sync()
        self.inspect.datatypes.experiments(project)

    def global_callback(self, func=None):
        """ Defines a callback to execute when collections of resources are 
            accessed.

            Parameters
            ----------
            func: callable
                A callable that takes the current collection object as first 
                argument and the current element object as second argument.

            Examples
            --------
            >>> def notify(cobj, eobj):
            >>>    print eobj._uri
            >>> interface.global_callback(notify)
        """
        self._callback = func

    def _setup_sqlites(self):
        self._lock = sqlutil.init_db(os.path.join(self._cachedir, 'lock.db'))

        sqlutil.create_table(self._lock, 'Lock', 
                             [('uri', 'TEXT PRIMARY KEY'), 
                              ('pid', 'INTEGER NOT NULL'),
                              ('date', 'REAL NOT NULL')],
                             commit=True
                            )

    def _connect(self):
        """ Sets up the connection with the XNAT server.
        """

        if DEBUG:   
            httplib2.debuglevel = 2
        self._conn = httplib2.Http(SQLCache(self._cachedir, self))
        self._conn.add_credentials(self._user, self._pwd)

    def _exec(self, uri, method='GET', body=None, headers=None):
        """ A wrapper around a simple httplib2.request call that:
                - avoids repeating the server url in the request
                - deals with custom caching mechanisms
                - manages a user session with cookies
                - catches and broadcast specific XNAT errors

            Parameters
            ----------
            uri: string
                URI of the resource to be accessed. e.g. /REST/projects
            method: GET | PUT | POST | DELETE
                HTTP method.
            body: string
                HTTP message body
            headers: dict
                Additional headers for the HTTP request.
        """
        if headers is None:
            headers = {}

        uri = join_uri(self._server, uri)
        try:
            sqlutil.insert(self._lock, 'Lock', (uri, os.getpid(), time.time()), commit=True)
        except Exception, e:
            opid, date = self._lock.execute('SELECT pid, date FROM Lock '
                                      'WHERE uri=?', (uri, )).fetchone()

            if opid == os.getpid() or time.time() - date > 10:
                sqlutil.delete(self._lock, 'Lock', 'uri', uri, commit=True)
            else:
                raise ResourceConcurrentAccessError(os.getpid(), opid, uri)

        # using session authentication
        headers['cookie'] = self._jsession

        # reset the memcache when something is changed on the server
        if method in ['PUT', 'DELETE']:
            self._memcache = {}
        
        if self._mode == 'online' and method == 'GET':
            if time.time() - self._memcache.get(uri, 0) < self._memtimeout:
                if DEBUG:
                    print 'send: GET CACHE %s'%uri
                info, content = self._conn.cache.get(uri).split('\r\n\r\n', 1)
                self._memcache[uri] = time.time()
                response = None
            else:
#                cached_value = self._conn.cache.get(uri)
#                make_request = False
#                if cached_value is not None:
#                    subject_id = re.findall('(?<=subjects/).*?(?=/.*)', uri)
#                    if subject_id != [] and subject_id[0] not in self.cache.diff():
#                        info, content = cached_value.split('\r\n\r\n', 1)
#                        self._memcache[uri] = time.time()
#                        response = None
#                    else:
#                        make_request = True
#                else:
#                    make_request = True

#                if make_request:
                start = time.time()
                response, content = self._conn.request(uri, method, body, headers)
                self._conn.cache.computation_times[uri] = time.time() - start
                self._memcache[uri] = time.time()

        elif self._mode == 'offline' and method == 'GET':
            cached_value = self._conn.cache.get(uri)
            if cached_value is not None:
                if DEBUG:
                    print 'send: GET CACHE %s'%uri
                info, content = cached_value.split('\r\n\r\n', 1)
                response = None
            else:
                try:
                    self._conn.timeout = 10
                    start = time.time()
                    response, content = self._conn.request(uri, method, 
                                                           body, headers)
                    self._conn.cache.computation_times[uri] = time.time() - start
                    self._conn.timeout = None
                    self._memcache[uri] = time.time()
                except Exception, e:
                    raise_exception(e)
        else:
            response, content = self._conn.request(uri, method, body, headers)

        if DEBUG:
            if response is None:
                response = httplib2.Response(email.message_from_string(info))
                print 'reply: %s %s from cache'%(response.status, response.reason)
                for key in response.keys():
                    print 'header: %s: %s'%(key.title(), response.get(key))

        if response is not None and response.has_key('set-cookie'):
            self._jsession = response.get('set-cookie')[:44]

        if response is not None and response.get('status') == '404':
            r,_ = self._conn.request(self._server)

            if self._server.rstrip('/') != r.get('content-location', self._server).rstrip('/'):
                old_server = self._server
                self._server = r.get('content-location').rstrip('/')
                return self._exec(uri.replace(old_server, ''), method, body)
            else:
                raise httplib2.HttpLib2Error('%s %s'%(response.status, response.reason))

        sqlutil.delete(self._lock, 'Lock', 'uri', uri, commit=True)

        return content


    def _get_json(self, uri):
        """ Specific Interface._exec method to retrieve data.
            It forces the data format to csv and then puts it back to a json-like
            format.
            
            Parameters
            ----------
            uri: string
                URI of the resource to be accessed. e.g. /REST/projects

            Returns
            -------
            List of dicts containing the results
        """
        if 'format=json' in uri:
            uri = uri.replace('format=json', 'format=csv')
        else:
            if '?' in uri:
                uri += '&format=csv'
            else:
                uri += '?format=csv'

        content = self._exec(uri, 'GET')

        if is_xnat_error(content):
            raise_exception(content)

        return csv_to_json(content)

    def save_config(self, location):
        fp = open(location, 'w')
        config = {'server':self._server, 
                  'user':self._user, 
                  'password':self._conn.credentials.credentials[0][2],
                  'cachedir':self._cachedir,
                  }

        json.dump(config, fp)
        fp.close()

