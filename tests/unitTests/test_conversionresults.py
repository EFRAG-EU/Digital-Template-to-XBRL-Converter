from time import sleep

import pytest

from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    Message,
    MessageType,
    Section,
    Severity,
    Stage,
)
from mireport.exceptions import EarlyAbortException


@pytest.fixture
def builder():
    return ConversionResultsBuilder(consoleOutput=False)


def test_add_message(builder):
    builder.addMessage("Something happened", Severity.INFO, MessageType.Conversion)
    assert len(builder.messages) == 1
    msg = builder.messages[0]
    assert msg.messageText == "Something happened"
    assert msg.severity == Severity.INFO
    assert msg.messageType == MessageType.Conversion


def test_processing_context_success_keeps_its_time_not_a_message(builder):
    with builder.processingContext("Success Test"):
        sleep(0.001)
    assert builder.messages == []
    built = builder.build()
    assert built.durationNs is not None and built.durationNs >= 1_000_000


def test_processing_context_early_abort_is_swallowed(builder):
    with builder.processingContext("Early Abort Test"):
        raise EarlyAbortException("Stopped")

    # Verify that a message indicating abort was recorded
    msgs = [m.messageText for m in builder.messages]
    assert any("aborted after" in m for m in msgs), "Abort message was not logged"


def test_processing_context_failure(builder):
    class TestError(Exception):
        pass

    with pytest.raises(TestError), builder.processingContext("Fail Test"):
        raise TestError("Fail")
    msgs = [m for m in builder.messages if m.severity == Severity.ERROR]
    assert any("finished abnormally" in m.messageText for m in msgs)


def test_serialization_round_trip(builder):
    builder.addMessage("Serialize", Severity.WARNING, MessageType.ExcelParsing)
    built = builder.build()
    d = built.toDict()
    rebuilt = built.fromDict(d)
    assert rebuilt.toDict() == d


def test_user_vs_dev_messages(builder):
    builder.addMessage("Dev", Severity.INFO, MessageType.DevInfo)
    builder.addMessage("User", Severity.WARNING, MessageType.ExcelParsing)
    assert "Dev" in [m.messageText for m in builder.developerMessages]
    assert "User" in [m.messageText for m in builder.userMessages]
    assert "Dev" not in [m.messageText for m in builder.userMessages]


def test_conversion_success_logic(builder):
    builder.addMessage("Oops", Severity.ERROR, MessageType.Conversion)
    assert not builder.conversionSuccessful

    builder = ConversionResultsBuilder()
    builder.addMessage("All good", Severity.INFO, MessageType.Conversion)
    assert builder.conversionSuccessful


def test_cell_tracking(builder):
    builder.addCellQueries({("Sheet1", 1, 1), ("Sheet1", 2, 2)})
    builder.addCellsWithData({("Sheet1", 1, 1)})
    assert builder.numCellQueries == 2
    assert builder.numCellsPopulated == 1


def test_add_message_with_concept_and_excel(builder):
    concept = "test:SomeConcept"
    builder.addMessage(
        "Conceptual issue",
        Severity.ERROR,
        MessageType.XbrlValidation,
        taxonomy_concept=concept,
        excel_reference="Sheet1!A2",
    )
    m = builder.messages[-1]
    assert m.conceptQName == "test:SomeConcept"
    assert m.excelReference == "Sheet1!A2"


@pytest.mark.parametrize("message_type", list(MessageType))
@pytest.mark.parametrize("severity", list(Severity))
def test_add_all_message_combinations_dont_crash(builder, message_type, severity):
    builder.addMessage(f"{severity.value} {message_type.value}", severity, message_type)
    assert builder.messages[-1].severity == severity
    assert builder.messages[-1].messageType == message_type


@pytest.mark.parametrize("severity", [Severity.INFO, Severity.WARNING, Severity.ERROR])
@pytest.mark.parametrize(
    "message_type", [MessageType.Conversion, MessageType.ExcelParsing]
)
def test_conversion_success_failure_logic(severity, message_type):
    builder = ConversionResultsBuilder()
    builder.addMessage(f"Msg: {severity}, {message_type}", severity, message_type)

    if severity is Severity.ERROR:
        assert not builder.conversionSuccessful
    else:
        assert builder.conversionSuccessful


def test_conversion_success_ignores_non_user_types(builder):
    builder.addMessage("Dev Info", Severity.ERROR, MessageType.DevInfo)
    builder.addMessage("Progress", Severity.WARNING, MessageType.Progress)
    assert builder.conversionSuccessful  # should be True


def test_conversion_success_mixed_relevant_and_irrelevant(builder):
    builder.addMessage("Dev Info", Severity.ERROR, MessageType.DevInfo)
    builder.addMessage("Conversion", Severity.WARNING, MessageType.Conversion)
    assert builder.conversionSuccessful


def test_conversion_failure_mixed_relevant_and_irrelevant(builder):
    builder.addMessage("Dev Info", Severity.ERROR, MessageType.DevInfo)
    builder.addMessage("Conversion", Severity.ERROR, MessageType.Conversion)
    assert not builder.conversionSuccessful


def test_conversion_success_empty(builder):
    assert builder.conversionSuccessful  # Nothing added, should be successful


def test_conversion_success_with_only_info(builder):
    builder.addMessage("Conversion Info", Severity.INFO, MessageType.Conversion)
    builder.addMessage("Parsing Info", Severity.INFO, MessageType.ExcelParsing)
    assert builder.conversionSuccessful


def test_conversion_success_with_xbrl_error(builder):
    builder.addMessage("XBRL error", Severity.ERROR, MessageType.XbrlValidation)
    assert builder.conversionSuccessful


def test_conversion_success_with_parsing_warning(builder):
    builder.addMessage("Parsing warning", Severity.WARNING, MessageType.ExcelParsing)
    assert builder.conversionSuccessful


def test_conversion_failure_with_parsing_error(builder):
    builder.addMessage("Parsing error", Severity.ERROR, MessageType.ExcelParsing)
    assert not builder.conversionSuccessful


def test_getOverallSeverity_just_xbrl_and_without_xbrl_behavior():
    b = ConversionResultsBuilder()
    b.addMessage("XBRL error", Severity.ERROR, MessageType.XbrlValidation)
    b.addMessage("Conversion warning", Severity.WARNING, MessageType.Conversion)
    result = b.build()

    # justXBRLValidation should reflect only the XBRL validation message(s)
    assert result.getOverallSeverity(justXBRLValidation=True) is Severity.ERROR
    assert result.getRAG(justXBRLValidation=True) == {
        "green": False,
        "amber": False,
        "red": True,
    }

    # withoutXBRLValidation should ignore XBRL validation messages and pick the conversion warning
    assert result.getOverallSeverity(withoutXBRLValidation=True) is Severity.WARNING
    assert result.getRAG(withoutXBRLValidation=True) == {
        "green": False,
        "amber": True,
        "red": False,
    }

    # default behaviour considers all relevant message types -> error wins
    assert result.getOverallSeverity() is Severity.ERROR
    assert result.getRAG() == {"green": False, "amber": False, "red": True}


def test_getOverallSeverity_empty_defaults_to_info_and_rag_mapping():
    b = ConversionResultsBuilder()
    r = b.build()
    assert r.getOverallSeverity() is Severity.INFO

    rag = r.getRAG()
    assert rag["green"] is True and rag["amber"] is False and rag["red"] is False


def test_getOverallSeverity_raises_on_conflicting_flags():
    b = ConversionResultsBuilder()
    r = b.build()
    with pytest.raises(ValueError):
        r.getOverallSeverity(withoutXBRLValidation=True, justXBRLValidation=True)


def test_getRAG_not_modifiaed_by_caller():
    b = ConversionResultsBuilder()
    r = b.build()
    rag = r.getRAG()

    with pytest.raises(TypeError):
        rag["red"] = True  # modify the returned dict (should raise)

    rag2 = r.getRAG()  # get it again
    assert (
        rag2["red"] is False
    )  # should not be affected by previous (attempted) modification


def test_empty_message_list_serialization():
    builder = ConversionResultsBuilder(conversionId="empty")
    result = builder.build()
    serialized = result.toDict()
    assert serialized["sections"] == []
    rebuilt = ConversionResults.fromDict(serialized)
    assert rebuilt.messages == []


def test_sections_collect_the_messages_added_while_open(builder):
    with builder.section(Stage.WORKBOOK):
        builder.addMessage("In the workbook", Severity.WARNING, MessageType.Conversion)
    with builder.section(Stage.PRIOR, source="2025.json"):
        builder.addMessages(
            [Message("Last year's", Severity.INFO, MessageType.Conversion)]
        )

    workbook, prior = builder.build().sections
    assert (workbook.stage, workbook.label, workbook.source) == (
        Stage.WORKBOOK,
        "Digital Template",
        None,
    )
    assert [m.messageText for m in workbook.messages] == ["In the workbook"]
    assert prior.title == "Last year's report (2025.json)"
    assert [m.messageText for m in prior.messages] == ["Last year's"]


def test_a_message_outside_any_section_goes_to_conversion(builder):
    builder.addMessage("Before", Severity.INFO, MessageType.Conversion)
    with builder.section(Stage.WORKBOOK):
        pass
    builder.addMessage("After", Severity.INFO, MessageType.Conversion)
    builder.addMessage("And after", Severity.INFO, MessageType.Conversion)

    sections = builder.build().sections
    assert [(s.stage, len(s.messages)) for s in sections] == [
        (Stage.CONVERSION, 1),
        (Stage.WORKBOOK, 0),
        (Stage.CONVERSION, 2),
    ]
    assert sections[0].label == "Conversion"


def test_a_section_records_its_duration_even_when_it_raises(builder):
    with pytest.raises(EarlyAbortException), builder.section(Stage.WORKBOOK):
        sleep(0.001)
        raise EarlyAbortException("Stopped")
    [section] = builder.sections
    assert section.durationNs is not None and section.durationNs >= 1_000_000


def test_sections_dont_nest(builder):
    with builder.section(Stage.PRIOR, source="a.json") as outer:
        with builder.section(Stage.PRIOR, source="a.json") as same:
            builder.addMessage("Inner", Severity.INFO, MessageType.Conversion)
        assert same is outer
        with (
            pytest.raises(ValueError, match="sections don't nest"),
            builder.section(Stage.WORD),
        ):
            pass
    assert [s.stage for s in builder.sections] == [Stage.PRIOR]
    assert [m.messageText for m in outer.messages] == ["Inner"]


def test_round_trip_keeps_the_sections(builder):
    with builder.section(Stage.WORD):
        builder.addMessage(
            "Could not be read.",
            Severity.ERROR,
            MessageType.Conversion,
            source="policy.docx",
        )
    built = builder.build()

    rebuilt = ConversionResults.fromDict(built.toDict())

    [section] = rebuilt.sections
    assert (section.stage, section.durationNs) == (
        Stage.WORD,
        built.sections[0].durationNs,
    )
    assert section.messages[0].source == "policy.docx"
    assert rebuilt.toDict() == built.toDict()


def test_results_stored_before_sections_are_one_conversion_section():
    old = {
        "id": "old",
        "m": [{"m": "Old", "s": "WARNING", "mt": "Conversion", "c": None, "e": None}],
        "q": 0,
        "p": 0,
        "success": True,
    }

    results = ConversionResults.fromDict(old)

    [section] = results.sections
    assert section.stage is Stage.CONVERSION
    assert [m.messageText for m in results.messages] == ["Old"]
    assert results.messages[0].source is None


def test_messages_and_rag_are_the_same_across_sections(builder):
    flat = ConversionResultsBuilder()
    for b, stage in ((builder, Stage.WORKBOOK), (flat, None)):
        if stage is None:
            b.addMessage("Warn", Severity.WARNING, MessageType.Conversion)
            b.addMessage("Bad", Severity.ERROR, MessageType.XbrlValidation)
        else:
            with b.section(stage):
                b.addMessage("Warn", Severity.WARNING, MessageType.Conversion)
            with b.section(Stage.PACKAGE):
                b.addMessage("Bad", Severity.ERROR, MessageType.XbrlValidation)
    sectioned, unsectioned = builder.build(), flat.build()

    assert [m.messageText for m in sectioned.messages] == ["Warn", "Bad"]
    assert sectioned.getRAG() == unsectioned.getRAG()
    assert sectioned.getRAG(withoutXBRLValidation=True)["amber"]
    assert sectioned.conversionSuccessful


def test_a_section_counts_its_messages_and_groups_them_by_file():
    section = Section(
        Stage.PDF,
        [
            Message("General", Severity.WARNING, MessageType.Conversion),
            Message("One", Severity.WARNING, MessageType.Conversion, source="b.pdf"),
            Message("Dev", Severity.INFO, MessageType.DevInfo, source="a.pdf"),
            Message("Two", Severity.ERROR, MessageType.Conversion, source="b.pdf"),
        ],
    )

    assert section.severityCounts() == "1 error, 2 warnings"
    assert section.severityCounts(developer=True) == "1 error, 2 warnings, 1 info"
    assert Section(Stage.PDF).severityCounts() == "no messages"
    assert [
        (source, [m.messageText for m in group])
        for source, group in section.messagesBySource(developer=True)
    ] == [(None, ["General"]), ("b.pdf", ["One", "Two"]), ("a.pdf", ["Dev"])]


def test_message_from_dict_invalid_enum():
    bad_data = {
        "m": "Bad message",
        "s": "NOT_A_SEVERITY",
        "mt": "Conversion",
        "c": None,
        "e": None,
    }

    with pytest.raises(KeyError):
        Message.fromDict(bad_data)


def test_add_message_with_qname_and_excel_reference(builder):
    builder.addMessage(
        "Info message",
        Severity.INFO,
        MessageType.Conversion,
        taxonomy_concept="mi:Something",
        excel_reference="A1",
    )

    m = builder.messages[-1]
    assert m.conceptQName == "mi:Something"
    assert m.excelReference == "A1"
    assert str(m).startswith("Info")


@pytest.fixture
def builder_with_console():
    return ConversionResultsBuilder(conversionId="ctx-test", consoleOutput=False)


def test_console_progress_is_printed_not_kept(capsys):
    builder = ConversionResultsBuilder(consoleOutput=True)
    with (
        builder.processingContext("Simple Task") as ctx,
        builder.section(Stage.PRIOR, source="2025.json"),
    ):
        sleep(0.001)  # simulate work

    assert ctx.succeeded
    assert builder.getMessages(wantedMessageTypes={MessageType.Progress}) == []
    printed = capsys.readouterr().out
    assert 'Starting: "Simple Task".' in printed
    assert "Starting: [Last year's report (2025.json)]." in printed
    assert "Finished: [Last year's report (2025.json)] in " in printed
    assert 'Finished: "Simple Task" in ' in printed


def test_nothing_is_printed_without_console_output(builder, capsys):
    with builder.processingContext("Quiet"), builder.section(Stage.WORKBOOK):
        pass
    assert capsys.readouterr().out == ""


def test_console_processing_context_early_abort_is_swallowed(builder_with_console):
    # It should NOT raise EarlyAbortException
    try:
        with builder_with_console.processingContext("Abortable Task"):
            raise EarlyAbortException("Done early")
    except EarlyAbortException:
        pytest.fail("EarlyAbortException should have been swallowed")

    messages = builder_with_console.getMessages(
        wantedMessageTypes={MessageType.Progress}
    )
    assert any("aborted after" in m.messageText for m in messages)


def test_processing_context_unexpected_exception(builder_with_console):
    with (
        pytest.raises(ValueError),
        builder_with_console.processingContext("Fails Hard") as ctx,
    ):
        raise ValueError("Unexpected")

    # Message should be logged with Severity.ERROR
    messages = builder_with_console.getMessages()
    assert any(m.severity == Severity.ERROR for m in messages)
    assert not ctx.succeeded


def test_isXbrlValid_no_xbrl_messages(builder):
    builder.addMessage("Conversion message", Severity.INFO, MessageType.Conversion)
    result = builder.build()
    assert not result.isXbrlValid


def test_isXbrlValid_with_info_xbrl_message(builder):
    builder = ConversionResultsBuilder()
    builder.addMessage("XBRL info", Severity.INFO, MessageType.XbrlValidation)
    result = builder.build()
    assert result.isXbrlValid


def test_isXbrlValid_with_warning_xbrl_message(builder):
    builder.addMessage("XBRL warning", Severity.WARNING, MessageType.XbrlValidation)
    result = builder.build()
    assert result.isXbrlValid


def test_isXbrlValid_with_error_xbrl_message(builder):
    builder.addMessage("XBRL error", Severity.ERROR, MessageType.XbrlValidation)
    result = builder.build()
    assert not result.isXbrlValid


def test_isXbrlValid_mixed_severities(builder):
    builder.addMessage("XBRL info", Severity.INFO, MessageType.XbrlValidation)
    builder.addMessage("XBRL warning", Severity.WARNING, MessageType.XbrlValidation)
    builder.addMessage("XBRL error", Severity.ERROR, MessageType.XbrlValidation)
    result = builder.build()
    assert not result.isXbrlValid


def test_isXbrlValid_mixed_message_types(builder):
    builder.addMessage("XBRL warning", Severity.WARNING, MessageType.XbrlValidation)
    builder.addMessage("Conversion error", Severity.ERROR, MessageType.Conversion)
    result = builder.build()
    # Only XBRL validation errors matter for isXbrlValid
    assert result.isXbrlValid


class TestExcelParsingWarningsAsDevInfo:
    @pytest.fixture
    def demoting(self):
        return ConversionResultsBuilder(excelParsingWarningsAsDevInfo=True)

    def test_excel_parsing_warning_becomes_dev_info(self, demoting):
        demoting.addMessage("Modified", Severity.WARNING, MessageType.ExcelParsing)
        (msg,) = demoting.messages
        assert msg.messageType is MessageType.DevInfo
        assert msg.severity is Severity.WARNING
        assert not demoting.userMessages
        assert demoting.developerMessages == [msg]
        assert not demoting.hasWarnings()

    @pytest.mark.parametrize(
        ("severity", "mtype"),
        [
            (Severity.ERROR, MessageType.ExcelParsing),
            (Severity.INFO, MessageType.ExcelParsing),
            (Severity.WARNING, MessageType.Conversion),
            (Severity.WARNING, MessageType.XbrlValidation),
        ],
    )
    def test_other_messages_unchanged(self, demoting, severity, mtype):
        demoting.addMessage("Other", severity, mtype)
        (msg,) = demoting.messages
        assert msg.messageType is mtype

    def test_added_messages_reclassified(self, demoting):
        demoting.addMessages(
            [
                Message("W", Severity.WARNING, MessageType.ExcelParsing, "c", "A1"),
                Message("E", Severity.ERROR, MessageType.ExcelParsing),
            ]
        )
        w, e = demoting.messages
        assert (w.messageType, w.severity) == (MessageType.DevInfo, Severity.WARNING)
        assert (w.messageText, w.conceptQName, w.excelReference) == ("W", "c", "A1")
        assert e.messageType is MessageType.ExcelParsing

    def test_off_by_default(self, builder):
        builder.addMessage("Modified", Severity.WARNING, MessageType.ExcelParsing)
        builder.addMessages([Message("W", Severity.WARNING, MessageType.ExcelParsing)])
        assert all(m.messageType is MessageType.ExcelParsing for m in builder.messages)
        assert builder.hasWarnings()
