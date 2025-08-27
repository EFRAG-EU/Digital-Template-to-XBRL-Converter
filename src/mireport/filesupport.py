import base64
import re
from io import BytesIO
from pathlib import Path
from typing import NamedTuple, Optional

from PIL import Image
from PIL.Image import Resampling

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


class FilelikeAndFileName(NamedTuple):
    fileContent: bytes
    filename: str

    def fileLike(self) -> BytesIO:
        return BytesIO(self.fileContent)

    def __str__(self) -> str:
        return f"{self.filename} [{format_bytes(len(self.fileContent))}]"

    def saveToFilepath(self, path: Path) -> None:
        """Saves the file content to the specified path."""
        parent = path.parent

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
        output_mime_type = "image/png"

        with Image.open(self.fileLike()) as img:
            # Fill in missing dimensions
            orig_width, orig_height = img.size
            target_width = orig_width if max_width is None else max_width
            target_height = orig_height if max_height is None else max_height

            img = img.convert("RGBA")  # Preserve transparency and unify mode
            img.thumbnail((target_width, target_height), Resampling.LANCZOS)

            bio = BytesIO()
            img.save(bio, format="PNG")
            base64_data = base64.b64encode(bio.getbuffer()).decode("ascii")

        return f"data:{output_mime_type};base64,{base64_data}"
