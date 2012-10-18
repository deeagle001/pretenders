import base64
import json
import re

def to_dict(wsgi_headers, include=lambda _: True):
    """
    Convert WSGIHeaders to a dict so that it can be JSON-encoded
    """
    ret = {}
    for k, v in wsgi_headers.items():
        if include(k):
            ret[k] = v
    return ret


class CaseInsensitiveDict(dict):
    "A dictionary that is case insensitive for keys."

    def __init__(self, *args, **kwargs):
        super(CaseInsensitiveDict, self).__init__(*args, **kwargs)
        for key, value in self.items():
            super(CaseInsensitiveDict, self).__delitem__(key)
            self[key.lower()] = value

    def __delitem__(self, key):
        return super(CaseInsensitiveDict, self).__delitem__(key.lower())

    def __setitem__(self, key, value):
        super(CaseInsensitiveDict, self).__setitem__(key.lower(), value)

    def __getitem__(self, key):
        return super(CaseInsensitiveDict, self).__getitem__(key.lower())


def binary_to_ascii(data):
    return base64.b64encode(data).decode('ascii')


def ascii_to_binary(data):
    return base64.b64decode(data.encode('ascii'))


class RequestSerialiser(object):
    """
    Utility class to proxy request from mock to boss.

    It is used to serialise requests as JSON data.
    """
    def __init__(self, path, request):
        if request.query_string:
            path = "{0}?{1}".format(path, request.query_string)

        self.body = binary_to_ascii(request.body.read())
        self.headers = to_dict(request.headers)
        self.method = request.method
        self.url = path
        self.rule = "{0} {1}".format(request.method, path)

    def serialize(self):
        data = {
            'body': self.body,
            'headers': self.headers,
            'method': self.method,
            'url': self.url,
            'rule': self.rule,
        }
        return json.dumps(data)


class JsonHelper(object):
    """
    Encapsulation of JSON data that can be reused for HTTP traffic.

    It can be initialised from JSON data or from detailed fields,
    that will end up as key/values in a dictionary.

    The JSON-ified data has some specific keys that are treated
    in a special way, as they represent parts of the HTTP requests
    and responses: ``headers``, ``body``, ``status``.

    ``body`` gets a special treatment, as it may contain binary
    data. It is converted to and from Base64 to make it serialisable
    in JSON.

    :param json_data:
        An optional string representing JSON data. It may include the
        following keys, for an HTTP preset:
    :param kwargs:
        Additional keyword arguments will complement or override the
        values in ``json_data``. Normally you will use one or the other.
    """
    def __init__(self, json_data=None, **kwargs):
        self.data = {}
        if json_data is not None:
            content = json_data.decode('ascii')
            self.data = json.loads(content)
        self.data.update(kwargs)

    def __getattr__(self, attribute):
        """Access attributes from JSON-dict as if they were class attibutes"""
        return self.data[attribute]

    @property
    def body(self):
        """The body field, as binary data"""
        return ascii_to_binary(self.data['body'])

    def as_dict(self):
        """The contained data, as a dictionary"""
        return self.data

    def as_http_response(self, response):
        """
        Generate an HTTP response from the data.

        ``body``, ``headers``, and ``status`` keys are used as such in
        the HTTP response.

        :param response:
            The ``bottle`` object that represents the response.
        """
        for header, value in self.headers.items():
            response.set_header(header, value)
        response.status = self.status
        return self.body

    def as_json(self):
        """The contained data, as a JSON-serialised string."""
        if isinstance(self.data['rule'], MatchRule):
            self.data['rule'] = self.data['rule'].as_dict()
        return json.dumps(self.data)

    def __str__(self):
        return str(self.data)


class Preset(JsonHelper):
    """A preset instance represents a pre-programmed response."""
    pass


def match_rule_from_dict(data):
    if isinstance(data, dict):
        return MatchRule(
            data['rule'], 
            data.get('headers', None),
            exact_headers=data.get('exact_headers', False)
        )

    else:
        return MatchRule(data)


class MatchRule(object):
    """
    Class encapsulating a matching rule against which incoming requests will 
    be compared.
    """

    DEFAULT_HEADERS = [
        'Content-Length', 'Content-Type', 'Host', 'Accept-Encoding'
    ]

    def __init__(self, rule, headers=None, **kwargs):
        """
        :param rule: String incorporating the method and url to be matched
            eg "GET url/to/match"
        :param headers: Dictionary of headers to match. 
            If headers is None then the rule will match on rule only,
                ignoring headers
            If headers is an empty dictionary ie {} the rule will match 
                only if the request has the basic, default headers.
        """
        self.rule = rule

        if 'exact_headers' in kwargs:
            # We must be hydrating a serialized dictionary 
            self.exact_headers = kwargs['exact_headers']
        else:
            self.exact_headers = True

        if headers:
            self.headers = headers
        else:
            if headers == None:
                self.exact_headers = False
            self.headers = {}

    def as_dict(self):
        """ Convert a match rule instance to a dictionary """
        return {
            'rule': self.rule, 
            'headers': self.headers, 
            'exact_headers': self.exact_headers
        }

    def __key(self):
        """ A unique key for a match rule which will be hashable. """
        keys = [self.rule, self.exact_headers]
        for k, v in self.headers.items():
            keys.append('{0}:{1}'.format(k, v))
        return tuple(keys)

    def __hash__(self):
        return hash(self.__key())

    def is_match(self, request):
        """
        Check if a given request matches the MatchRule instance.
        
        :param request:  A dictionary representing a mock request.
        :return: True if the request is a match for rule and False if not.
        """
        return  self.is_rule_match(request) and self.is_header_match(request)

    def is_rule_match(self, request):
        """ 
        Check if a provided request matches the regex in the rule attribute 
        :param request:  A dictionary representing a mock request.
        :return: True if the request is a match for rule and False if not.
        """
        return re.match(self.rule, request['rule']) != None

    def is_header_match(self, request):
        """ 
        Check if a provided request matches the configured headers.

        :param request:  A dictionary representing a mock request.
        :return: True if the request is a match for rule and False if not.
        """
        is_matched = True
        if self.headers:
            for k, v in self.headers.items():
                try:
                    request_header = request['headers'][k]
                except KeyError:
                    is_matched = False
                    break
                else:
                    if request_header != v:
                        is_matched = False
                        break

        if self.exact_headers and is_matched:
            expected_headers = []
            expected_headers.extend(self.headers.keys())
            expected_headers.extend(self.DEFAULT_HEADERS)

            diff = set(expected_headers).symmetric_difference(
                set(request['headers'].keys()))
            if diff:
                is_matched = False
        
        return is_matched


class MockHttpRequest(JsonHelper):
    """A stored HTTP request as issued to our pretend server"""
    def __init__(self, pretend_response):
        if pretend_response.status != 200:
            # TODO use custom exception
            raise Exception('No saved request')
        super(MockHttpRequest, self).__init__(pretend_response.read())
