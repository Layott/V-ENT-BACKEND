"""Storage backends.

`private_storage` keeps identity documents out of MEDIA_ROOT entirely.

Everything under MEDIA_ROOT is served straight off disk by nginx, so anything
written there is public to anyone who knows (or guesses) the filename. KYC
uploads are government IDs, so they live under PRIVATE_MEDIA_ROOT instead, with
no `base_url`: calling `.url` on such a file raises, which is deliberate - it
turns "somebody rendered a KYC image straight into a template" into a loud
error instead of a silent leak. Reads go through `vent_auth.views_kyc_files`.

Permissions are set here rather than left to FILE_UPLOAD_PERMISSIONS, because
that setting is global and public media needs the opposite of what these files
need. Public uploads must be world readable or nginx cannot serve them; an ID
document must not be. 0o640 with the directory group set to nginx's user lets
the X-Accel-Redirect read the file while nothing else on the box can.
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


def private_storage():
    """Callable so migrations serialize a reference, not an absolute path."""
    return FileSystemStorage(
        location=settings.PRIVATE_MEDIA_ROOT,
        base_url=None,
        file_permissions_mode=0o640,
        directory_permissions_mode=0o750,
    )
