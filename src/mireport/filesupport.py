import base64
import re
from collections.abc import Iterable
from io import BytesIO, UnsupportedOperation
from pathlib import Path
from typing import BinaryIO, NamedTuple, Optional

from PIL import Image, UnidentifiedImageError
from PIL.Image import Resampling
from typing_extensions import Buffer

from mireport.stringutil import format_bytes

ZIP_UNWANTED_RE = re.compile(r"[^\w.]+")  # \w includes '_'
FILE_UNWANTED_RE = re.compile(r'[<>:"/\\|?*]')


def is_valid_filename(filename: str) -> bool:
    """Checks if the filename is valid for Windows."""
    # Disallowed names (case-insensitive)
    reserved_names = {
        "CON",
        "AUX",
        "NUL",
        "PRN",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    # Ensure filename is not "." or ".."
    if filename in {".", ".."}:
        return False

    # Ensure filename does not match a reserved name (case-insensitive)
    if filename.upper() in reserved_names:
        return False

    # Ensure filename does not contain invalid characters
    if FILE_UNWANTED_RE.search(filename):
        return False

    return True


def zipSafeString(original: str, fallback: str = "fallback") -> str:
    # Use no-args version of split to replace one or more whitespace chars with
    # underscore
    new = "_".join(original.split())
    new = ZIP_UNWANTED_RE.sub("_", new)
    if not (new and is_valid_filename(new)):
        new = fallback
    return new


class NamedBytesIO(BytesIO):
    """
    An in-memory binary stream with a required name attribute.
    Compatible with file-like consumers expecting BinaryIO or BytesIO.
    """

    name: str

    def __init__(self, content: bytes, *, name: str) -> None:
        super().__init__(content)
        self.name = name

    def __repr__(self) -> str:
        payload = self.getbuffer()
        size = len(payload)
        peek = bytes(payload[: 2**4])
        return (
            f"{self.__class__.__name__}(name={self.name!r}, size={size}, peek={peek!r})"
        )

    def __str__(self) -> str:
        return f'"{self.name}" [{format_bytes(len(self.getbuffer()))}]'


class ReadOnlyNamedBytesIO(NamedBytesIO):
    """
    A read-only in-memory binary stream with a required name attribute.
    Prevents mutation via write, truncate, or buffer access.
    Compatible with file-like consumers expecting .name and .read().
    """

    def getbuffer(self) -> memoryview:
        """Get a read-only view over the contents of the BytesIO object."""
        return super().getbuffer().toreadonly()

    def truncate(self, _: Optional[int] = None) -> int:
        raise UnsupportedOperation("This BytesIO is read-only")

    def writable(self) -> bool:
        return False

    def write(self, _: bytes | Buffer) -> int:
        raise UnsupportedOperation("This BytesIO is read-only")

    def writelines(self, _: Iterable[bytes | Buffer]) -> None:
        raise UnsupportedOperation("This BytesIO is read-only")


class FilelikeAndFileName(NamedTuple):
    """
    Immutable, in-memory holder of file data and file metadata (just the
    filename at present).

    Contains various convenience methods that arrange the file data and metadata
    as required either for other libraries or for export.

    Serialises well and without special methods due to underlying tuple
    structure.
    """

    fileContent: bytes
    filename: str

    def __str__(self) -> str:
        return f'"{self.filename}" [{format_bytes(len(self.fileContent))}]'

    def fileLike(self, writable: bool = False) -> BinaryIO:
        """
        Returns a Python file-like object for use with APIs that expect a
        file-like object (read() and .name in particular).

        :param writable: If True, returns a mutable file-like object. If False,
        returns a read-only file-like object.

        The file-like object may or may not be mutable but any changes made to
        it have no affect on the original FilelikeAndFileName.
        """
        if writable:
            return NamedBytesIO(self.fileContent, name=self.filename)
        else:
            return ReadOnlyNamedBytesIO(self.fileContent, name=self.filename)

    def saveToFilepath(self, path: Path) -> None:
        """Saves the file content to the specified path."""
        parent = path.parent

        if not is_valid_filename(path.name):
            raise ValueError(f"Filename {path.name} is not valid")

        # Check if parent directory exists and is actually a file
        if parent.exists() and parent.is_file():
            raise ValueError(
                f"Parent path {parent} is an existing file, not a directory"
            )

        # Check if parent directory exists
        if not parent.exists():
            raise ValueError(f"Parent directory {parent} does not exist")

        with open(path, "wb") as f:
            f.write(self.fileContent)
            f.flush()
        assert f.closed, "File should be closed after writing"
        return

    def saveToDirectory(self, directory: Path) -> None:
        """Saves the file content to the specified directory using @self.filename."""
        if directory.exists() and directory.is_file():
            raise ValueError(f"Path {directory} is an existing file, not a directory")

        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
        self.saveToFilepath(directory / self.filename)
        return


class ImageFileLikeAndFileName(FilelikeAndFileName):
    """
    Variant of FilelikeAndFileName that has additional methods related to image
    support.
    """

    def can_open_image(self) -> bool:
        """
        Checks if the file content is a valid image that can be converted to a
        data URL.

        :return: True if the file content is an image we support, False otherwise.
        """
        try:
            with Image.open(self.fileLike()):
                pass
            return True
        except UnidentifiedImageError:
            return False

    def as_data_url(
        self,
        max_width: Optional[int] = None,
        max_height: Optional[int] = None,
    ) -> str:
        """
        Resize and convert a logo image to a base64 data URL suitable for XHTML embedding.
        Always outputs PNG for maximum compatibility.

        :param logo_data: FilelikeAndFileName containing the image data.
        :param max_width: Maximum width in pixels.
        :param max_height: Maximum height in pixels.
        :return: A data URL string (image/png).
        """
        # Always output PNG
        output_mime_type, output_format = "image/png", "PNG"

        fio = self.fileLike()
        try:
            with Image.open(fio) as img:
                # Fill in missing dimensions
                orig_width, orig_height = img.size
                target_width = orig_width if max_width is None else max_width
                target_height = orig_height if max_height is None else max_height

                img = img.convert("RGBA")  # Preserve transparency and unify mode
                img.thumbnail((target_width, target_height), Resampling.LANCZOS)

                bio = BytesIO()
                img.save(bio, format=output_format)
                base64_data = base64.b64encode(bio.getbuffer()).decode("ascii")
                return f"data:{output_mime_type};base64,{base64_data}"
        except UnidentifiedImageError as e:
            raise ValueError(
                f"Cannot convert file {fio} to data URL: not a supported/valid image"
            ) from e
