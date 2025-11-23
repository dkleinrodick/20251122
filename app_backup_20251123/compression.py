import zlib
import base64
import json

def compress_data(data_obj):
    """Compresses a dict/list into a base64 string."""
    json_str = json.dumps(data_obj)
    compressed = zlib.compress(json_str.encode('utf-8'))
    return base64.b64encode(compressed).decode('ascii')

def decompress_data(data_stored):
    """Decompresses a base64 string back into a dict/list. Handles uncompressed legacy data."""
    if isinstance(data_stored, (dict, list)):
        return data_stored # Already uncompressed
    
    if isinstance(data_stored, str):
        try:
            # Try to decompress
            compressed = base64.b64decode(data_stored)
            json_str = zlib.decompress(compressed).decode('utf-8')
            return json.loads(json_str)
        except (zlib.error, base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            # Fallback: might be a plain JSON string?
            try:
                return json.loads(data_stored)
            except:
                return data_stored # Return raw as last resort
    
    return data_stored