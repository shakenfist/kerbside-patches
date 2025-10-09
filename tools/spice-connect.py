# A quick and dirty command line validator for SPICE console connection
# information. I'd very much like to replace this with an actually
# scriptable SPICE client, but that would need to exist first.
#
# Arguments are:
#     spice-connect.py host non-tls-port

import socket
import struct
import sys


# SPICE client protocol constants
magic = b'REDQ'
major = 2
minor = 2
main_channel = 1
common_caps = 11  # AuthSelection, AuthSpice, MiniHeader
channel_caps = 9  # SemiSeamlessMigrate, SeamlessMigrate


if __name__ == '__main__':
    # Connect to the specified non-TLS port and verify we get back
    # a SPICE protocol greeting
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((sys.argv[1], int(sys.argv[2])))

    # Send a client greeting
    #
    # ---- SpiceLinkMess ----
    # 4s    UINT32 magic value, must be REDQ
    # I     UINT32 major_version, must be 2
    # I     UINT32 minor_version, must be 2
    # I     UINT32 size number of bytes following this field to the end
    #              of this message.
    # I     UINT32 connection_id. In case of a new session (i.e., channel
    #              type is SPICE_CHANNEL_MAIN) this field is set to zero,
    #              and in response the server will allocate session id
    #              and will send it via the SpiceLinkReply message. In
    #              case of all other channel types, this field will be
    #              equal to the allocated session id.
    # B     UINT8  channel_type, we use main
    # B     UINT8  channel_id to connect to
    # I     UINT32 num_common_caps number of common client channel
    #              capabilities words
    # I     UINT32 num_channel_caps number of specific client channel
    #              capabilities words
    # I     UINT32 caps_offset location of the start of the capabilities
    #              vector given by the bytes offset from the “size”
    #              member (i.e., from the address of the “connection_id”
    #              member).
    # ...          capabilities
    sock.sendall(struct.pack(
        '<4sIIIIBBIIIII', magic, major, minor, 42 - 16,
        0, main_channel, 0, 1, 1, 18, common_caps,
        channel_caps))

    # ---- SpiceLinkReply ----
    # 4s     UINT32 magic value, must be equal to SPICE_MAGIC
    # I      UINT32 major_version, must be equal to SPICE_VERSION_MAJOR
    # I      UINT32 minor_version, must be equal to SPICE_VERSION_MINOR
    # I      UINT32 size number of bytes following this field to the end
    #               of this message.
    # I      UINT32 error code
    # ...
    buffered = sock.recv(20)
    (
        server_magic, server_major, server_minor, _, server_error
    ) = struct.unpack_from('<4sIIII', buffered)

    assert server_magic == b'REDQ'
    assert server_major == 2
    assert server_minor == 2
    
    # NOTE(mikal): this check is commented out because Kerbside returns an
    # error on the insecure port that indicates a reconnection on the
    # secure port is required.
    # assert server_error == 0

    print('OK')