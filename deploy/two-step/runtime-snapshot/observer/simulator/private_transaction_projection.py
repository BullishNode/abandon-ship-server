"""Strip original native SQL transaction witnesses before public source export.

Inputs remain private to the collector. This checks byte identity and encoding,
not chain confirmation, ownership, reserve classification or terminal settlement.
"""
import hashlib
import re

from .bundle_audit import decode_transaction
from .model import InvariantError, public_payload

MAX_PRIVATE_TRANSACTION_BYTES = 1024*1024


def project_private_transaction(txid, original):
    """Return only a txid-preserving body, original weight and private-byte hash."""
    if not isinstance(txid,str) or re.fullmatch('[0-9a-f]{64}',txid) is None:
        raise ValueError('exact native transaction id required')
    if not isinstance(original,bytes) or not 0<len(original)<=MAX_PRIVATE_TRANSACTION_BYTES:
        raise ValueError('bounded original private transaction bytes required')
    decoded=decode_transaction(original.hex(),public_body=True)
    if decoded['txid']!=txid:
        raise InvariantError('native SQL transaction id differs from original body')
    if any(decoded['input_script_sig_sizes']):
        raise ValueError('public SQL projection refuses nonempty scriptSig')
    result={'txid':txid,'transaction_body':decoded['stripped_hex'],
        'encoding':'bitcoin-stripped-body-v1',
        'original_signed_weight_wu':decoded['weight'],
        'original_signed_size_bytes':len(original),
        'original_signed_sha256':hashlib.sha256(original).hexdigest()}
    public_payload(result)
    return result
