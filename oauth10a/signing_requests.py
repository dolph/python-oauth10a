"""
All Token requests and Protected Resources requests MUST be signed by the
Consumer and verified by the Service Provider. The purpose of signing requests
is to prevent unauthorized parties from using the Consumer Key and Tokens when
making Token requests or Protected Resources requests. The signature process
encodes the Consumer Secret and Token Secret into a verifiable value which is
included with the request.

OAuth does not mandate a particular signature method, as each implementation
can have its own unique requirements. The protocol defines three signature
methods: HMAC-SHA1, RSA-SHA1, and PLAINTEXT, but Service Providers are free to
implement and document their own methods. Recommending any particular method is
beyond the scope of this specification.

The Consumer declares a signature method in the oauth_signature_method
parameter, generates a signature, and stores it in the oauth_signature
parameter. The Service Provider verifies the signature as specified in each
method. When verifying a Consumer signature, the Service Provider SHOULD check
the request nonce to ensure it has not been used in a previous Consumer
request.

The signature process MUST NOT change the request parameter names or values,
with the exception of the oauth_signature parameter.

"""

import base64
import hashlib
import hmac
import urllib
import urlparse

import requests

from oauth10a import utils


def request_url(url):
    """9.1.2: Construct Request URL

    The Signature Base String includes the request absolute URL, tying the
    signature to a specific endpoint. The URL used in the Signature Base
    String MUST include the scheme, authority, and path, and MUST exclude
    the query and fragment as defined by [RFC3986] section 3.

    If the absolute request URL is not available to the Service Provider
    (it is always available to the Consumer), it can be constructed by
    combining the scheme being used, the HTTP Host header, and the relative
    HTTP request URL. If the Host header is not available, the Service
    Provider SHOULD use the host name communicated to the Consumer in the
    documentation or other means.

    The Service Provider SHOULD document the form of URL used in the
    Signature Base String to avoid ambiguity due to URL normalization.
    Unless specified, URL scheme and authority MUST be lowercase and
    include the port number; http default port 80 and https default port
    443 MUST be excluded.

    For example, the request:

        HTTP://Example.com:80/resource?id=123

    Is included in the Signature Base String as:

        http://example.com/resource

    """
    # urlparse correctly handles case insensitivity here
    parsed = urlparse.urlparse(url)

    # canonicalize the port if redundant with scheme
    netloc = [parsed.hostname]
    port = parsed.port
    if parsed.scheme == 'http' and port == 80:
        port = None
    if parsed.scheme == 'https' and port == 443:
        port = None
    if port is not None:
        netloc.append(str(port))
    netloc = ':'.join(netloc)

    return urlparse.urlunparse(
        (parsed.scheme, netloc, parsed.path, None, None, None))


def normalized_request_parameters(oauth_params, get_params=None,
                                  post_params=None):
    """The request parameters are collected, sorted and concatenated into a
    normalized string:

    - Parameters in the OAuth HTTP Authorization header excluding the realm
        parameter.
    - Parameters in the HTTP POST request body (with a content-type of
        application/x-www-form-urlencoded).
    - HTTP GET parameters added to the URLs in the query part (as defined
        by [RFC3986] section 3).

    The oauth_signature parameter MUST be excluded.

    The parameters are normalized into a single string as follows:

    Parameters are sorted by name, using lexicographical byte value
    ordering. If two or more parameters share the same name, they are
    sorted by their value. For example:

        a=1, c=hi%20there, f=25, f=50, f=a, z=p, z=t

    Parameters are concatenated in their sorted order into a single string.
    For each parameter, the name is separated from the corresponding value
    by an '=' character (ASCII code 61), even if the value is empty. Each
    name-value pair is separated by an '&' character (ASCII code 38). For
    example:

        a=1&c=hi%20there&f=25&f=50&f=a&z=p&z=t

    """
    # oauth_signature parameter MUST be excluded
    oauth_params.pop('oauth_signature', None)

    additional_params = [d for d in [get_params, post_params] if d is not None]

    params = []
    for d in [oauth_params] + additional_params:
        for k in d.keys():
            params.append((str(k), str(d[k])))

    params.sort()

    return '&'.join(['='.join(param) for param in params])


def signature_base_string(http_method, url, oauth_params, get_params=None,
                          post_params=None):
    """9.1.3: Concatenate Request Elements

    The following items MUST be concatenated in order into a single string.
    Each item is encoded and separated by an '&' character (ASCII code 38),
    even if empty.

    - The HTTP request method used to send the request. Value MUST be
        uppercase, for example: HEAD, GET, POST, etc.
    - The request URL from Section 9.1.2.
    - The normalized request parameters string from Section 9.1.1.

    See Signature Base String example in Appendix A.5.1.

    """
    http_method = http_method.upper()
    url = request_url(url)
    params = normalized_request_parameters(
        oauth_params, get_params, post_params)

    request_elements = [http_method, url, params]
    request_elements = map(urllib.quote_plus, request_elements)
    return '&'.join(request_elements)


class AuthBase(requests.auth.AuthBase):
    def __init__(self, consumer_key, consumer_secret, token_key=None,
                 token_secret=None, callback_url='oob'):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.token_key = token_key or ''
        self.token_secret = token_secret or ''
        self.callback_url = callback_url

    def signature_base_string(self, r, oauth_params):
        """Consistent reproducible concatenation of the request elements.

        The string is used as an input in hashing or signing algorithms.

        """
        return signature_base_string(r.method, r.url, oauth_params)

    def __call__(self, r):
        oauth_params = {
            'oauth_consumer_key': self.consumer_key,
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp': str(utils.timestamp()),
            'oauth_nonce': utils.nonce(),
            'oauth_version': '1.0',
            'oauth_callback': self.callback_url,
        }
        oauth_signature = self.generate_signature(r, oauth_params)
        oauth_params['oauth_signature'] = oauth_signature
        params = urllib.urlencode(oauth_params)

        if r.method == 'GET':
            # FIXME: assuming no existing query params
            r.url = r.url + '?' + params
        else:
            r.headers['Authorization'] = 'OAuth %s' % params
            # FIXME: missing WWW-Authenticate header
            # r.headers['WWW-Authenticate']
        return r


class HMACSHA1Auth(AuthBase):
    """9.2: HMAC-SHA1

    The HMAC-SHA1 signature method uses the HMAC-SHA1 signature algorithm as
    defined in [RFC2104] where the Signature Base String is the text and the
    key is the concatenated values (each first encoded per Parameter Encoding)
    of the Consumer Secret and Token Secret, separated by an '&' character
    (ASCII code 38) even if empty.

    """
    def __init__(self, *args, **kwargs):
        super(HMACSHA1Auth, self).__init__(*args, **kwargs)
        self.digestmod = hashlib.sha1

    def generate_signature(self, r, oauth_params):
        """9.2.1: Generating Signature

        `oauth_signature` is set to the calculated digest octet string, first
        base64-encoded per [RFC2045] section 6.8, then URL-encoded per
        Parameter Encoding by AuthBase.

        """
        signing_key = '%s&%s' % (
            urllib.quote_plus(self.consumer_secret),
            urllib.quote_plus(self.token_secret))

        s = self.signature_base_string(r, oauth_params)
        s = hmac.new(signing_key, msg=s, digestmod=self.digestmod).digest()
        s = base64.b64encode(s)
        return s


class RSASHA1Auth(AuthBase):
    def __init__(self, *args, **kwargs):
        super(RSASHA1Auth, self).__init__(*args, **kwargs)

    def __call__(self, r):
        """9.3.1: Generating Signature

        The Signature Base String is signed using the Consumer's RSA private
        key per [RFC3447] section 8.2.1, where K is the Consumer's RSA private
        key, M the Signature Base String, and S is the result signature octet
        string:

            S = RSASSA-PKCS1-V1_5-SIGN (K, M)

        oauth_signature is set to S, first base64-encoded per [RFC2045] section
        6.8, then URL-encoded per Parameter Encoding.

        """
        return r


class PlaintextAuth(AuthBase):
    """9.4: Plaintext

    The PLAINTEXT method does not provide any security protection and SHOULD
    only be used over a secure channel such as HTTPS. It does not use the
    Signature Base String.

    """

    def __init__(self, *args, **kwargs):
        super(PlaintextAuth, self).__init__(*args, **kwargs)

    def __call__(self, r):
        """9.4.1: Generating Signature

        `oauth_signature` is set to the concatenated encoded values of the
        Consumer Secret and Token Secret, separated by a '&' character (ASCII
        code 38), even if either secret is empty. The result MUST be encoded
        again.

        These examples show the value of `oauth_signature` for Consumer Secret
        `djr9rjt0jd78jf88` and 3 different Token Secrets:

        jjd999tj88uiths3:

            oauth_signature=djr9rjt0jd78jf88%26jjd999tj88uiths3

        jjd99$tj88uiths3:

            oauth_signature=djr9rjt0jd78jf88%26jjd99%2524tj88uiths3

        Empty:

            oauth_signature=djr9rjt0jd78jf88%26

        """
        return r
