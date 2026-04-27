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

# ── is_valid_filename ─────────────────────────────────────────────────────────


class TestIsValidFilename:
    @pytest.mark.parametrize(
        "filename",
        [
            "test.txt",
            "example_file.doc",
            "file.name.ext",
            "valid-file_name.txt",
            "another.valid_file-name.md",
        ],
        ids=["simple", "underscore", "multi-dot", "dash-and-underscore", "mixed"],
    )
    def test_valid(self, filename: str) -> None:
        assert is_valid_filename(filename)

    def test_dot_and_dotdot(self) -> None:
        assert not is_valid_filename(".")
        assert not is_valid_filename("..")

    def test_reserved_names(self) -> None:
        assert not is_valid_filename("CON")
        assert not is_valid_filename("con")
        assert not is_valid_filename("AuX")
        for i in range(1, 10):
            assert not is_valid_filename(f"COM{i}")
            assert not is_valid_filename(f"LPT{i}")

    @pytest.mark.parametrize(
        "filename",
        ["my|file.txt", "bad:file", "invalid*name.doc"],
        ids=["pipe", "colon", "asterisk"],
    )
    def test_invalid_characters(self, filename: str) -> None:
        assert not is_valid_filename(filename)


# ── zipSafeString ─────────────────────────────────────────────────────────────


class TestZipSafeString:
    @pytest.mark.parametrize(
        "input_text, expected",
        [
            ("good filename.txt", "good_filename.txt"),
            ("normal_name.doc", "normal_name.doc"),
        ],
        ids=["space-to-underscore", "no-change"],
    )
    def test_valid_input(self, input_text: str, expected: str) -> None:
        assert zipSafeString(input_text) == expected

    def test_whitespace_normalization(self) -> None:
        assert zipSafeString("some    file   name.txt") == "some_file_name.txt"

    @pytest.mark.parametrize(
        "input_text, expected",
        [
            ("bad/file:name*here.txt", "bad_file_name_here.txt"),
            ("weird#name?.ext", "weird_name_.ext"),
        ],
        ids=["slash-colon-asterisk", "hash-question"],
    )
    def test_invalid_characters_replacement(
        self, input_text: str, expected: str
    ) -> None:
        assert zipSafeString(input_text) == expected

    def test_reserved_fallback(self) -> None:
        assert zipSafeString("CON") == "fallback"

    def test_empty_input(self) -> None:
        assert zipSafeString("") == "fallback"

    def test_custom_fallback(self) -> None:
        assert zipSafeString("CON", fallback="default") == "default"


# ── FilelikeAndFileName ───────────────────────────────────────────────────────


class TestFilelikeAndFileName:
    def test_creation(self) -> None:
        file_obj = FilelikeAndFileName(b"Hello, World!", "test.txt")
        assert file_obj.fileContent == b"Hello, World!"
        assert file_obj.filename == "test.txt"

    @pytest.mark.parametrize(
        "content, filename, expected",
        [
            (b"Some test data", "example.doc", '"example.doc" [14 B]'),
            (b"", "empty.txt", '"empty.txt" [0 B]'),
            (b"x" * 1024, "large.bin", '"large.bin" [1 KiB]'),
        ],
        ids=["normal", "empty", "large"],
    )
    def test_str(self, content: bytes, filename: str, expected: str) -> None:
        assert str(FilelikeAndFileName(content, filename)) == expected

    def test_fileLike(self) -> None:
        content = b"Test content for BytesIO"
        file_obj = FilelikeAndFileName(content, "test.txt")
        bio = file_obj.fileLike()
        assert bio.read() == content
        assert bio.tell() == len(content)
        bio2 = file_obj.fileLike()
        assert bio2.read() == content
        assert bio is not bio2

    def test_fileLike_returns_ReadOnlyNamedBytesIO(self) -> None:
        content = b"ReadOnlyNamedBytesIO content"
        file_obj = FilelikeAndFileName(content, "readonly.bin")
        bio = file_obj.fileLike()
        assert isinstance(bio, ReadOnlyNamedBytesIO)
        assert bio.read() == content
        assert bio.name == "readonly.bin"
        assert not bio.writable()
        bio.seek(0)
        assert bio.read(5) == content[:5]

    def test_fileLike_is_read_only(self) -> None:
        bio = FilelikeAndFileName(b"abc", "file.txt").fileLike()
        with pytest.raises(UnsupportedOperation, match="read-only"):
            bio.write(b"def")
        with pytest.raises(UnsupportedOperation, match="read-only"):
            bio.truncate()
        mv = bio.getbuffer()
        assert isinstance(mv, memoryview)
        assert mv.readonly
        assert bytes(mv) == b"abc"

    def test_fileLike_independent_instances(self) -> None:
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

    def test_fileLike_closed_behavior(self) -> None:
        bio = FilelikeAndFileName(b"123", "closed.txt").fileLike()
        bio.close()
        assert bio.closed
        with pytest.raises(ValueError):
            bio.read()

    def test_fileLike_writable(self) -> None:
        original = b"origin"
        file_obj = FilelikeAndFileName(original, "file.txt")
        bio = file_obj.fileLike(writable=True)
        assert isinstance(bio, NamedBytesIO)
        bio.seek(0, 2)
        bio.write(b"_appended")
        bio.seek(0)
        assert bio.read().endswith(b"_appended")
        assert file_obj.fileContent == original

    def test_namedtuple_behavior(self) -> None:
        content = b"NamedTuple test"
        file_obj = FilelikeAndFileName(content, "tuple_test.txt")
        assert file_obj[0] == content
        assert file_obj[1] == "tuple_test.txt"
        unpacked_content, unpacked_filename = file_obj
        assert unpacked_content == content
        assert unpacked_filename == "tuple_test.txt"
        assert file_obj._fields == ("fileContent", "filename")

    def test_immutability(self) -> None:
        file_obj = FilelikeAndFileName(b"Immutable test", "immutable.txt")
        with pytest.raises(AttributeError):
            file_obj.fileContent = b"Modified"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            file_obj.filename = "modified.txt"  # type: ignore[misc]

    def test_saveToFilepath_creates_file(self) -> None:
        content = b"Test file content"
        file_obj = FilelikeAndFileName(content, "test_save.txt")
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "output.txt"
            file_obj.saveToFilepath(file_path)
            assert file_path.exists()
            assert file_path.read_bytes() == content

    def test_saveToFilepath_overwrites_existing(self) -> None:
        new_content = b"New content"
        file_obj = FilelikeAndFileName(new_content, "test.txt")
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "existing.txt"
            file_path.write_bytes(b"Original content")
            file_obj.saveToFilepath(file_path)
            assert file_path.read_bytes() == new_content

    def test_saveToFilepath_parent_file_error(self) -> None:
        file_obj = FilelikeAndFileName(b"Parent file test", "output.txt")
        with tempfile.TemporaryDirectory() as temp_dir:
            blocking_file = Path(temp_dir) / "blocking_file.txt"
            blocking_file.write_text("I'm blocking the path!")
            with pytest.raises(
                ValueError, match="is an existing file, not a directory"
            ):
                file_obj.saveToFilepath(blocking_file / "output.txt")

    def test_saveToFilepath_parent_does_not_exist(self) -> None:
        file_obj = FilelikeAndFileName(b"Missing parent test", "output.txt")
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing" / "directories" / "output.txt"
            with pytest.raises(ValueError, match="Parent directory .* does not exist"):
                file_obj.saveToFilepath(missing_path)

    def test_saveToFilepath_binary_content(self) -> None:
        content = bytes(range(256))
        file_obj = FilelikeAndFileName(content, "binary.bin")
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "binary_test.bin"
            file_obj.saveToFilepath(file_path)
            assert file_path.read_bytes() == content

    def test_saveToDirectory_creates_file(self) -> None:
        content = b"Directory save test"
        filename = "dir_test.txt"
        file_obj = FilelikeAndFileName(content, filename)
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "subdir"
            file_obj.saveToDirectory(directory)
            expected_path = directory / filename
            assert expected_path.exists()
            assert expected_path.read_bytes() == content

    def test_saveToDirectory_creates_nested_directories(self) -> None:
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

    def test_saveToDirectory_existing_directory(self) -> None:
        content = b"Existing dir test"
        filename = "existing_dir_test.txt"
        file_obj = FilelikeAndFileName(content, filename)
        with tempfile.TemporaryDirectory() as temp_dir:
            file_obj.saveToDirectory(Path(temp_dir))
            expected_path = Path(temp_dir) / filename
            assert expected_path.exists()
            assert expected_path.read_bytes() == content

    def test_saveToDirectory_empty_content(self) -> None:
        file_obj = FilelikeAndFileName(b"", "empty.txt")
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "empty_test"
            file_obj.saveToDirectory(directory)
            expected_path = directory / "empty.txt"
            assert expected_path.exists()
            assert expected_path.read_bytes() == b""

    def test_saveToDirectory_special_filename(self) -> None:
        content = b"Special filename test"
        filename = "my_file.name.with.dots.txt"
        file_obj = FilelikeAndFileName(content, filename)
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "special"
            file_obj.saveToDirectory(directory)
            expected_path = directory / filename
            assert expected_path.exists()
            assert expected_path.read_bytes() == content

    def test_saveToDirectory_existing_file_path_error(self) -> None:
        file_obj = FilelikeAndFileName(b"File path test", "output.txt")
        with tempfile.TemporaryDirectory() as temp_dir:
            existing_file = Path(temp_dir) / "existing_file.txt"
            existing_file.write_text("I'm a file, not a directory!")
            with pytest.raises(
                ValueError, match="is an existing file, not a directory"
            ):
                file_obj.saveToDirectory(existing_file)

    def test_saveToDirectory_file_extension_in_path(self) -> None:
        content = b"Extension test"
        filename = "result.txt"
        file_obj = FilelikeAndFileName(content, filename)
        with tempfile.TemporaryDirectory() as temp_dir:
            file_like_path = Path(temp_dir) / "looks_like_file.txt"
            file_obj.saveToDirectory(file_like_path)
            assert file_like_path.is_dir()
            expected_file = file_like_path / filename
            assert expected_file.exists()
            assert expected_file.read_bytes() == content


# ── ReadOnlyNamedBytesIO ──────────────────────────────────────────────────────


class TestReadOnlyNamedBytesIO:
    def test_name_and_content(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"sample data", name="sample.txt")
        assert bio.name == "sample.txt"
        assert bio.read() == b"sample data"

    def test_seek_and_tell(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"abcdef", name="abc.txt")
        assert bio.tell() == 0
        bio.seek(3)
        assert bio.tell() == 3
        assert bio.read() == b"def"
        bio.seek(0)
        assert bio.read(2) == b"ab"

    def test_writable_and_write_raises(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"123", name="file.bin")
        assert not bio.writable()
        with pytest.raises(UnsupportedOperation, match="read-only"):
            bio.write(b"456")

    def test_truncate_raises(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"abc", name="file.txt")
        with pytest.raises(UnsupportedOperation, match="read-only"):
            bio.truncate()

    def test_getbuffer_readonly(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"abc", name="file.txt")
        mv = bio.getbuffer()
        assert isinstance(mv, memoryview)
        assert mv.readonly
        assert bytes(mv) == b"abc"

    def test_closed_behavior(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"xyz", name="file.txt")
        assert not bio.closed
        bio.close()
        assert bio.closed
        with pytest.raises(ValueError):
            bio.read()

    def test_multiple_instances_independent(self) -> None:
        content = b"hello"
        bio1 = ReadOnlyNamedBytesIO(content, name="file.txt")
        bio2 = ReadOnlyNamedBytesIO(content, name="file.txt")
        assert bio1 is not bio2
        assert bio1.read() == content
        bio2.seek(0)
        assert bio2.read(2) == b"he"

    def test_repr_and_str(self) -> None:
        bio = ReadOnlyNamedBytesIO(b"abc", name="file.txt")
        repr_str = repr(bio)
        assert "ReadOnlyNamedBytesIO" in repr_str
        assert "name='file.txt'" in repr_str
        assert "size=3" in repr_str
        assert "peek=" in repr_str
        str_str = str(bio)
        assert "file.txt" in str_str
        assert "3 B" in str_str


# ── NamedBytesIO ──────────────────────────────────────────────────────────────


class TestNamedBytesIO:
    def test_name_content_and_mutability(self) -> None:
        content = b"abcdef"
        bio = NamedBytesIO(content, name="mutable.bin")
        assert bio.name == "mutable.bin"
        assert bio.writable()
        bio.seek(0, 2)
        bio.write(b"XYZ")
        bio.seek(0)
        assert bio.read() == content + b"XYZ"
        bio.truncate(4)
        bio.seek(0)
        assert bio.read() == b"abcd"
        mv = bio.getbuffer()
        assert isinstance(mv, memoryview)
        assert not mv.readonly
        assert bytes(mv) == b"abcd"

    def test_str_contains_name_and_size(self) -> None:
        bio = NamedBytesIO(b"x" * 150, name="big.bin")
        s = str(bio)
        assert "big.bin" in s
        assert "150 B" in s or "KiB" in s
