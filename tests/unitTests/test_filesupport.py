import tempfile
from io import UnsupportedOperation
from pathlib import Path

import pytest

from mireport.filesupport import (
    FilelikeAndFileName,
    NamedBytesIO,
    ReadOnlyNamedBytesIO,
    is_valid_filename,
    zipSafeString,
)


def test_is_valid_filename_valid_cases() -> None:
    assert is_valid_filename("test.txt")
    assert is_valid_filename("example_file.doc")
    assert is_valid_filename("file.name.ext")


def test_is_valid_filename_dot_and_dotdot() -> None:
    assert not is_valid_filename(".")
    assert not is_valid_filename("..")


def test_is_valid_filename_reserved_names() -> None:
    assert not is_valid_filename("CON")
    assert not is_valid_filename("con")
    assert not is_valid_filename("AuX")
    for i in range(1, 10):
        assert not is_valid_filename(f"COM{i}")
        assert not is_valid_filename(f"LPT{i}")


def test_is_valid_filename_invalid_characters() -> None:
    assert not is_valid_filename("my|file.txt")
    assert not is_valid_filename("bad:file")
    assert not is_valid_filename("invalid*name.doc")


def test_is_valid_filename_valid_misc_characters() -> None:
    assert is_valid_filename("valid-file_name.txt")
    assert is_valid_filename("another.valid_file-name.md")


def test_zipSafeString_valid_input() -> None:
    assert zipSafeString("good filename.txt") == "good_filename.txt"
    assert zipSafeString("normal_name.doc") == "normal_name.doc"


def test_zipSafeString_whitespace_normalization() -> None:
    assert zipSafeString("some    file   name.txt") == "some_file_name.txt"


def test_zipSafeString_invalid_characters_replacement() -> None:
    assert zipSafeString("bad/file:name*here.txt") == "bad_file_name_here.txt"
    assert zipSafeString("weird#name?.ext") == "weird_name_.ext"


def test_zipSafeString_reserved_fallback() -> None:
    assert zipSafeString("CON") == "fallback"


def test_zipSafeString_empty_input() -> None:
    assert zipSafeString("") == "fallback"


def test_zipSafeString_custom_fallback() -> None:
    assert zipSafeString("CON", fallback="default") == "default"


def test_FilelikeAndFileName_creation() -> None:
    """Test basic creation and properties."""
    content = b"Hello, World!"
    filename = "test.txt"
    file_obj = FilelikeAndFileName(content, filename)

    assert file_obj.fileContent == content
    assert file_obj.filename == filename


def test_FilelikeAndFileName_fileLike() -> None:
    """Test fileLike() method returns proper BytesIO."""
    content = b"Test content for BytesIO"
    file_obj = FilelikeAndFileName(content, "test.txt")

    bio = file_obj.fileLike()
    assert bio.read() == content
    assert bio.tell() == len(content)

    # Test that it's a fresh BytesIO each time
    bio2 = file_obj.fileLike()
    assert bio2.read() == content
    assert bio is not bio2  # Different instances


def test_FilelikeAndFileName_str() -> None:
    """Test string representation."""
    content = b"Some test data"
    filename = "example.doc"
    file_obj = FilelikeAndFileName(content, filename)

    expected = f'"{filename}" [{len(content)} B]'
    assert str(file_obj) == expected


def test_FilelikeAndFileName_str_empty_content() -> None:
    """Test string representation with empty content."""
    file_obj = FilelikeAndFileName(b"", "empty.txt")
    assert str(file_obj) == '"empty.txt" [0 B]'


def test_FilelikeAndFileName_str_large_content() -> None:
    """Test string representation with large content."""
    content = b"x" * 1024
    file_obj = FilelikeAndFileName(content, "large.bin")
    assert str(file_obj) == '"large.bin" [1 KiB]'


def test_saveToFilepath_creates_file() -> None:
    """Test saveToFilepath creates file with correct content."""
    content = b"Test file content"
    filename = "test_save.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "output.txt"
        file_obj.saveToFilepath(file_path)

        assert file_path.exists()
        assert file_path.read_bytes() == content


def test_saveToFilepath_overwrites_existing() -> None:
    """Test saveToFilepath overwrites existing files."""
    original_content = b"Original content"
    new_content = b"New content"
    file_obj = FilelikeAndFileName(new_content, "test.txt")

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "existing.txt"

        # Create existing file
        file_path.write_bytes(original_content)
        assert file_path.read_bytes() == original_content

        # Overwrite with new content
        file_obj.saveToFilepath(file_path)
        assert file_path.read_bytes() == new_content


def test_saveToFilepath_parent_file_error() -> None:
    """Test saveToFilepath when parent path is an existing file."""
    content = b"Parent file test"
    file_obj = FilelikeAndFileName(content, "output.txt")

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a file that we'll try to use as a parent directory
        blocking_file = Path(temp_dir) / "blocking_file.txt"
        blocking_file.write_text("I'm blocking the path!")

        # Try to save to a path where the parent is a file
        bad_path = blocking_file / "output.txt"

        with pytest.raises(ValueError, match="is an existing file, not a directory"):
            file_obj.saveToFilepath(bad_path)


def test_saveToFilepath_parent_does_not_exist() -> None:
    """Test saveToFilepath when parent directory doesn't exist."""
    content = b"Missing parent test"
    file_obj = FilelikeAndFileName(content, "output.txt")

    with tempfile.TemporaryDirectory() as temp_dir:
        # Try to save to a path where parent directories don't exist
        missing_path = Path(temp_dir) / "missing" / "directories" / "output.txt"

        with pytest.raises(ValueError, match="Parent directory .* does not exist"):
            file_obj.saveToFilepath(missing_path)


def test_saveToFilepath_binary_content() -> None:
    """Test saveToFilepath handles binary content correctly."""
    # Test with binary data including null bytes
    content = bytes(range(256))
    file_obj = FilelikeAndFileName(content, "binary.bin")

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "binary_test.bin"
        file_obj.saveToFilepath(file_path)

        assert file_path.read_bytes() == content


def test_saveToDirectory_creates_file() -> None:
    """Test saveToDirectory creates file in directory."""
    content = b"Directory save test"
    filename = "dir_test.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir) / "subdir"
        file_obj.saveToDirectory(directory)

        expected_path = directory / filename
        assert expected_path.exists()
        assert expected_path.read_bytes() == content


def test_saveToDirectory_creates_nested_directories() -> None:
    """Test saveToDirectory creates nested directories."""
    content = b"Nested directory test"
    filename = "nested_test.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir) / "level1" / "level2" / "level3"
        file_obj.saveToDirectory(directory)

        assert directory.exists()
        expected_path = directory / filename
        assert expected_path.exists()
        assert expected_path.read_bytes() == content


def test_saveToDirectory_existing_directory() -> None:
    """Test saveToDirectory works with existing directory."""
    content = b"Existing dir test"
    filename = "existing_dir_test.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)  # Use existing temp directory
        file_obj.saveToDirectory(directory)

        expected_path = directory / filename
        assert expected_path.exists()
        assert expected_path.read_bytes() == content


def test_saveToDirectory_empty_content() -> None:
    """Test saveToDirectory handles empty content."""
    file_obj = FilelikeAndFileName(b"", "empty.txt")

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir) / "empty_test"
        file_obj.saveToDirectory(directory)

        expected_path = directory / "empty.txt"
        assert expected_path.exists()
        assert expected_path.read_bytes() == b""


def test_saveToDirectory_special_filename() -> None:
    """Test saveToDirectory with filename containing dots and underscores."""
    content = b"Special filename test"
    filename = "my_file.name.with.dots.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir) / "special"
        file_obj.saveToDirectory(directory)

        expected_path = directory / filename
        assert expected_path.exists()
        assert expected_path.read_bytes() == content


def test_saveToDirectory_existing_file_path_error() -> None:
    """Test saveToDirectory behavior when passed an existing file path."""
    content = b"File path test"
    filename = "output.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create an existing file
        existing_file = Path(temp_dir) / "existing_file.txt"
        existing_file.write_text("I'm a file, not a directory!")

        # Try to use it as a directory - this should fail
        with pytest.raises(ValueError, match="is an existing file, not a directory"):
            file_obj.saveToDirectory(existing_file)


def test_saveToDirectory_file_extension_in_path() -> None:
    """Test saveToDirectory when path looks like a file (has extension)."""
    content = b"Extension test"
    filename = "result.txt"
    file_obj = FilelikeAndFileName(content, filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Path that looks like a file but doesn't exist
        file_like_path = Path(temp_dir) / "looks_like_file.txt"

        # Should still work - creates directory with that name
        file_obj.saveToDirectory(file_like_path)

        assert file_like_path.is_dir()
        expected_file = file_like_path / filename
        assert expected_file.exists()
        assert expected_file.read_bytes() == content


def test_namedtuple_behavior() -> None:
    """Test that FilelikeAndFileName behaves as a NamedTuple."""
    content = b"NamedTuple test"
    filename = "tuple_test.txt"
    file_obj = FilelikeAndFileName(content, filename)

    # Test indexing
    assert file_obj[0] == content
    assert file_obj[1] == filename

    # Test unpacking
    unpacked_content, unpacked_filename = file_obj
    assert unpacked_content == content
    assert unpacked_filename == filename

    # Test _fields attribute
    assert file_obj._fields == ("fileContent", "filename")


def test_immutability() -> None:
    """Test that FilelikeAndFileName is immutable (NamedTuple behavior)."""
    content = b"Immutable test"
    filename = "immutable.txt"
    file_obj = FilelikeAndFileName(content, filename)

    # Should not be able to modify fields
    try:
        file_obj.fileContent = b"Modified"
        assert False, "Should not be able to modify fileContent"
    except AttributeError:
        pass  # Expected

    try:
        file_obj.filename = "modified.txt"
        assert False, "Should not be able to modify filename"
    except AttributeError:
        pass  # Expected


def test_FilelikeAndFileName_fileLike_returns_ReadOnlyNamedBytesIO() -> None:
    """Test fileLike() returns a ReadOnlyNamedBytesIO with correct properties."""
    content = b"ReadOnlyNamedBytesIO content"
    filename = "readonly.bin"
    file_obj = FilelikeAndFileName(content, filename)

    bio = file_obj.fileLike()
    assert isinstance(bio, ReadOnlyNamedBytesIO)
    assert bio.read() == content
    assert bio.name == filename
    assert not bio.writable()
    bio.seek(0)
    assert bio.read(5) == content[:5]


def test_FilelikeAndFileName_fileLike_is_read_only() -> None:
    """Test fileLike() returns a truly read-only BytesIO."""
    file_obj = FilelikeAndFileName(b"abc", "file.txt")
    bio = file_obj.fileLike()
    with pytest.raises(UnsupportedOperation, match="read-only"):
        bio.write(b"def")
    with pytest.raises(UnsupportedOperation, match="read-only"):
        bio.truncate()
    mv = bio.getbuffer()
    assert isinstance(mv, memoryview)
    assert mv.readonly
    assert bytes(mv) == b"abc"


def test_FilelikeAndFileName_fileLike_independent_instances() -> None:
    """Test fileLike() returns new ReadOnlyNamedBytesIO instances each call."""
    content = b"independent"
    file_obj = FilelikeAndFileName(content, "indep.txt")
    bio1 = file_obj.fileLike()
    bio2 = file_obj.fileLike()
    assert bio1 is not bio2
    assert bio1.read() == content
    assert bio2.read() == content
    bio1.seek(0)
    assert bio1.read(3) == content[:3]
    bio2.seek(0)
    assert bio2.read(3) == content[:3]


def test_FilelikeAndFileName_fileLike_closed_behavior() -> None:
    """Test ReadOnlyNamedBytesIO returned by fileLike() handles close correctly."""
    file_obj = FilelikeAndFileName(b"123", "closed.txt")
    bio = file_obj.fileLike()
    bio.close()
    assert bio.closed
    with pytest.raises(ValueError):
        bio.read()


def test_ReadOnlyNamedBytesIO_name_and_content() -> None:
    """Test that ReadOnlyNamedBytesIO exposes .name and correct content."""
    content = b"sample data"
    name = "sample.txt"
    bio = ReadOnlyNamedBytesIO(content, name=name)
    assert bio.name == name
    assert bio.read() == content


def test_ReadOnlyNamedBytesIO_seek_and_tell() -> None:
    """Test seek and tell work as expected."""
    content = b"abcdef"
    bio = ReadOnlyNamedBytesIO(content, name="abc.txt")
    assert bio.tell() == 0
    bio.seek(3)
    assert bio.tell() == 3
    assert bio.read() == b"def"
    bio.seek(0)
    assert bio.read(2) == b"ab"


def test_ReadOnlyNamedBytesIO_writable_and_write_raises() -> None:
    """Test writable() returns False and write raises."""
    bio = ReadOnlyNamedBytesIO(b"123", name="file.bin")
    assert not bio.writable()
    with pytest.raises(UnsupportedOperation, match="read-only"):
        bio.write(b"456")


def test_ReadOnlyNamedBytesIO_truncate_raises() -> None:
    """Test truncate raises UnsupportedOperation."""
    bio = ReadOnlyNamedBytesIO(b"abc", name="file.txt")
    with pytest.raises(UnsupportedOperation, match="read-only"):
        bio.truncate()


def test_ReadOnlyNamedBytesIO_getbuffer_readonly() -> None:
    """Test getbuffer returns a readonly buffer with correct content."""
    bio = ReadOnlyNamedBytesIO(b"abc", name="file.txt")
    mv = bio.getbuffer()
    assert isinstance(mv, memoryview)
    assert mv.readonly
    assert bytes(mv) == b"abc"


def test_ReadOnlyNamedBytesIO_closed_behavior() -> None:
    """Test closed property and reading after close."""
    bio = ReadOnlyNamedBytesIO(b"xyz", name="file.txt")
    assert not bio.closed
    bio.close()
    assert bio.closed
    with pytest.raises(ValueError):
        bio.read()


def test_ReadOnlyNamedBytesIO_multiple_instances_independent() -> None:
    """Test multiple ReadOnlyNamedBytesIO instances are independent."""
    content = b"hello"
    name = "file.txt"
    bio1 = ReadOnlyNamedBytesIO(content, name=name)
    bio2 = ReadOnlyNamedBytesIO(content, name=name)
    assert bio1 is not bio2
    assert bio1.read() == content
    bio2.seek(0)
    assert bio2.read(2) == b"he"


def test_ReadOnlyNamedBytesIO_repr_and_str() -> None:
    """Test __repr__ and __str__ for debugging."""
    bio = ReadOnlyNamedBytesIO(b"abc", name="file.txt")
    repr_str = repr(bio)
    # __repr__ should contain class name, name, size, and peek
    assert "ReadOnlyNamedBytesIO" in repr_str
    assert "name='file.txt'" in repr_str
    assert "size=3" in repr_str
    assert "peek=" in repr_str
    # __str__ is inherited from NamedBytesIO
    str_str = str(bio)
    assert "file.txt" in str_str
    assert "3 B" in str_str


# NamedBytesIO tests
def test_NamedBytesIO_name_content_and_mutability() -> None:
    """NamedBytesIO should expose .name, allow mutation and reflect changes."""
    content = b"abcdef"
    name = "mutable.bin"
    bio = NamedBytesIO(content, name=name)

    assert bio.name == name
    # writable should be True for NamedBytesIO
    assert bio.writable()
    # write at end should append
    bio.seek(0, 2)
    bio.write(b"XYZ")
    bio.seek(0)
    assert bio.read() == content + b"XYZ"

    # truncate should work
    bio.truncate(4)
    bio.seek(0)
    assert bio.read() == b"abcd"

    # getbuffer should return a memoryview reflecting current content
    mv = bio.getbuffer()
    assert isinstance(mv, memoryview)
    assert not mv.readonly
    assert bytes(mv) == b"abcd"


def test_FilelikeAndFileName_fileLike_writable_returns_NamedBytesIO_and_is_independent() -> (
    None
):
    """fileLike(writable=True) should return a NamedBytesIO that does not mutate the source tuple."""
    original = b"origin"
    filename = "file.txt"
    file_obj = FilelikeAndFileName(original, filename)

    bio = file_obj.fileLike(writable=True)
    assert isinstance(bio, NamedBytesIO)
    # append some data
    bio.seek(0, 2)
    bio.write(b"_appended")
    bio.seek(0)
    assert bio.read().endswith(b"_appended")

    # original FilelikeAndFileName must remain unchanged
    assert file_obj.fileContent == original


def test_NamedBytesIO_str_contains_name_and_size() -> None:
    content = b"x" * 150
    name = "big.bin"
    bio = NamedBytesIO(content, name=name)
    s = str(bio)
    assert name in s
    assert "150 B" in s or "KiB" in s  # depends on format_bytes output
