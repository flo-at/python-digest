from __future__ import absolute_import
from __future__ import unicode_literals

import hashlib
import random

from collections import namedtuple

import six
from six.moves import range
from six.moves.urllib.parse import urlparse, unquote

from python_digest.utils import parse_parts, format_parts

_REQUIRED_DIGEST_RESPONSE_PARTS = [
    'username', 'realm', 'nonce', 'uri', 'response', 'algorithm', 'opaque', 'qop', 'nc', 'cnonce'
]
DigestResponse = namedtuple(
    'DigestResponse',
    _REQUIRED_DIGEST_RESPONSE_PARTS
)

_REQUIRED_DIGEST_CHALLENGE_PARTS = ['realm', 'nonce', 'stale', 'algorithm', 'opaque', 'qop']
DigestChallenge = namedtuple('DigestChallenge', _REQUIRED_DIGEST_CHALLENGE_PARTS)

_AVAILABLE_HASH_FUNCS = ['MD5', 'SHA-256', 'SHA-512']
if 'sha512_256' in hashlib.algorithms_available:
    _AVAILABLE_HASH_FUNCS.append('SHA-512-256')

def validate_uri(digest_uri, request_path):
    digest_url_components = urlparse(digest_uri)
    return unquote(digest_url_components[2]) == request_path

def get_hash_func(algorithm):
    if algorithm == 'SHA-256':
        return hashlib.sha256
    elif algorithm == 'SHA-512-256':
        def hash_wrapper(data):
            hash_obj = hashlib.new('sha512_256')
            hash_obj.update(data)
            return hash_obj
        return hash_wrapper
    elif algorithm == 'SHA-512':
        return hashlib.sha512
    # default to 'MD5'
    return hashlib.md5

def validate_nonce(nonce, secret):
    '''
    Is the nonce one that was generated by this library using the provided secret?
    '''
    nonce_components = nonce.split(':', 2)
    if not len(nonce_components) == 3:
        return False
    timestamp = nonce_components[0]
    salt = nonce_components[1]
    nonce_signature = nonce_components[2]

    calculated_nonce = calculate_nonce(timestamp, secret, salt)

    if not nonce == calculated_nonce:
        return False

    return True

def calculate_partial_digest(username, realm, password, algorithm='MD5'):
    '''
    Calculate a partial digest that may be stored and used to authenticate future
    HTTP Digest sessions.
    '''
    if isinstance(realm, six.text_type):
        realm = realm.encode('utf-8')
    hash_func = get_hash_func(algorithm)
    return hash_func(b"%s:%s:%s" % (username.encode('utf-8'), realm, password.encode('utf-8'))).hexdigest()

def build_digest_challenge(timestamp, secret, realm, opaque, stale, algorithm='MD5'):
    '''
    Builds a Digest challenge that may be sent as the value of the 'WWW-Authenticate' header
    in a 401 or 403 response.
    
    'opaque' may be any value - it will be returned by the client.

    'timestamp' will be incorporated and signed in the nonce - it may be retrieved from the
    client's authentication request using get_nonce_timestamp()
    '''
    nonce = calculate_nonce(timestamp, secret)

    return 'Digest %s' % format_parts(realm=realm, qop='auth', nonce=nonce,
                                      opaque=opaque, algorithm=algorithm,
                                      stale=stale and 'true' or 'false')

def calculate_request_digest(method, partial_digest, digest_response=None,
                             uri=None, nonce=None, nonce_count=None, client_nonce=None, algorithm=None):
    '''
    Calculates a value for the 'response' value of the client authentication request.
    Requires the 'partial_digest' calculated from the realm, username, and password.

    Either call it with a digest_response to use the values from an authentication request,
    or pass the individual parameters (i.e. to generate an authentication request).
    '''
    if digest_response:
        if uri or nonce or nonce_count or client_nonce or algorithm:
            raise Exception("Both digest_response and one or more "
                            "individual parameters were sent.")
        uri = digest_response.uri
        nonce = digest_response.nonce
        nonce_count = digest_response.nc
        client_nonce=digest_response.cnonce
        algorithm = digest_response.algorithm
    elif not (uri and nonce and (nonce_count != None) and client_nonce):
        raise Exception("Neither digest_response nor all individual parameters were sent.")
    algorithm = algorithm or 'MD5'
        
    hash_func = get_hash_func(algorithm)
    ha2 = hash_func(("%s:%s" % (method, uri)).encode('utf-8')).hexdigest()
    data = "%s:%s:%s:%s:%s" % (nonce, "%08x" % nonce_count, client_nonce, 'auth', ha2)
    kd = hash_func(("%s:%s" % (partial_digest, data)).encode('utf-8')).hexdigest()
    return kd

def get_nonce_timestamp(nonce):
    '''
    Extract the timestamp from a Nonce. To be sure the timestamp was generated by this site,
    make sure you validate the nonce using validate_nonce().
    '''
    components = nonce.split(':', 2)
    if not len(components) == 3:
        return None

    try:
        return float(components[0])
    except ValueError:
        return None

def calculate_nonce(timestamp, secret, salt=None):
    '''
    Generate a nonce using the provided timestamp, secret, and salt. If the salt is not provided,
    (and one should only be provided when validating a nonce) one will be generated randomly
    in order to ensure that two simultaneous requests do not generate identical nonces.
    '''
    if not salt:
        salt = ''.join([random.choice('0123456789ABCDEF') for x in range(4)])
    return "%s:%s:%s" % (
        timestamp,
        salt,
        hashlib.md5(("%s:%s:%s" % (timestamp, salt, secret)).encode('utf-8')).hexdigest()
    )

def build_authorization_request(username, method, uri, nonce_count, digest_challenge=None,
                                realm=None, nonce=None, opaque=None, password=None,
                                request_digest=None, client_nonce=None, algorithm=None):
    '''
    Builds an authorization request that may be sent as the value of the 'Authorization'
    header in an HTTP request.

    Either a digest_challenge object (as returned from parse_digest_challenge) or its required
    component parameters (nonce, realm, opaque) must be provided.

    The nonce_count should be the last used nonce_count plus one.
    
    Either the password or the request_digest should be provided - if provided, the password
    will be used to generate a request digest. The client_nonce is optional - if not provided,
    a random value will be generated.
    '''
    if not client_nonce:
        client_nonce =  ''.join([random.choice('0123456789ABCDEF') for x in range(32)])

    if digest_challenge and (realm or nonce or opaque or algorithm):
        raise Exception("Both digest_challenge and one or more of realm, nonce, opaque and algorithm"
                        "were sent.")

    if digest_challenge:
        if six.PY2 and isinstance(digest_challenge, bytes):
            digest_challenge = digest_challenge.decode('utf-8')
        if isinstance(digest_challenge, six.text_type):
            digest_challenge_header = digest_challenge
            digest_challenge = parse_digest_challenge(digest_challenge_header)
            if not digest_challenge:
                raise Exception("The provided digest challenge header could not be parsed: %s" %
                                digest_challenge_header)
        realm = digest_challenge.realm
        nonce = digest_challenge.nonce
        opaque = digest_challenge.opaque
        algorithm = digest_challenge.algorithm
    elif not (realm and nonce and opaque):
        raise Exception("Either digest_challenge or realm, nonce, and opaque must be sent.")
        
    algorithm = algorithm or 'MD5'
    if password and request_digest:
        raise Exception("Both password and calculated request_digest were sent.")
    elif not request_digest:
        if not password:
            raise Exception("Either password or calculated request_digest must be provided.")
            
        partial_digest = calculate_partial_digest(username, realm, password, algorithm)
        request_digest = calculate_request_digest(method, partial_digest, uri=uri, nonce=nonce,
                                                  nonce_count=nonce_count,
                                                  client_nonce=client_nonce,
                                                  algorithm=algorithm)

    return 'Digest %s' % format_parts(username=username, realm=realm, nonce=nonce, uri=uri,
                                      response=request_digest, algorithm=algorithm, opaque=opaque,
                                      qop='auth', nc='%08x' % nonce_count, cnonce=client_nonce)
    
def _check_required_parts(parts, required_parts):
    if parts == None:
        return False

    missing_parts = [part for part in required_parts if not part in parts]
    return len(missing_parts) == 0

    
def parse_digest_response(digest_response_string):
    '''
    Parse the parameters of a Digest response. The input is a comma separated list of
    token=(token|quoted-string). See RFCs 2616 and 2617 for details.

    Known issue: this implementation will fail if there are commas embedded in quoted-strings.
    '''

    parts = parse_parts(digest_response_string, defaults={'algorithm': 'MD5'})
    if not _check_required_parts(parts, _REQUIRED_DIGEST_RESPONSE_PARTS):
        return None

    if not parts['nc'] or [c for c in parts['nc'] if not c in '0123456789abcdefABCDEF']:
        return None
    parts['nc'] = int(parts['nc'], 16)

    digest_response = DigestResponse(**{
        part_name: part for part_name, part in six.iteritems(parts)
        if part_name in _REQUIRED_DIGEST_RESPONSE_PARTS
    })
    if digest_response.algorithm not in _AVAILABLE_HASH_FUNCS:
        return None
    if 'auth' != digest_response.qop:
        return None
                
    return digest_response

def is_digest_credential(authorization_header):
    '''
    Determines if the header value is potentially a Digest response sent by a client (i.e.
    if it starts with 'Digest ' (case insensitive).
    '''
    return authorization_header[:7].lower() == 'digest '

def parse_digest_credentials(authorization_header):
    '''
    Parses the value of an 'Authorization' header. Returns an object with properties
    corresponding to each of the recognized parameters in the header.
    '''
    if not is_digest_credential(authorization_header):
        return None

    return parse_digest_response(authorization_header[7:])

def is_digest_challenge(authentication_header):
    '''
    Determines if the header value is potentially a Digest challenge sent by a server (i.e.
    if it starts with 'Digest ' (case insensitive).
    '''
    return authentication_header[:7].lower() == 'digest '

def parse_digest_challenge(authentication_header):
    '''
    Parses the value of a 'WWW-Authenticate' header. Returns an object with properties
    corresponding to each of the recognized parameters in the header.
    '''
    if not is_digest_challenge(authentication_header):
        return None

    parts = parse_parts(authentication_header[7:], defaults={'algorithm': 'MD5',
                                                             'stale': 'false'})
    if not _check_required_parts(parts, _REQUIRED_DIGEST_CHALLENGE_PARTS):
        return None

    parts['stale'] = parts['stale'].lower() == 'true'

    digest_challenge = DigestChallenge(**{
        part_name: part for part_name, part in six.iteritems(parts)
        if part_name in _REQUIRED_DIGEST_CHALLENGE_PARTS
    })
    if digest_challenge.algorithm not in _AVAILABLE_HASH_FUNCS:
        return None
    if 'auth' != digest_challenge.qop:
        return None

    return digest_challenge
