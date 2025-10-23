from time import sleep

import pytest

from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    Message,
    MessageType,
    Severity,
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


def test_processing_context_success(builder):
    with builder.processingContext("Success Test"):
        pass
    msgs = [m.messageText for m in builder.messages]
    assert any('Finished: "Success Test"' in m for m in msgs)


def test_processing_context_early_abort_is_swallowed(builder):
    with builder.processingContext("Early Abort Test"):
        raise EarlyAbortException("Stopped")

    # Verify that a message indicating abort was recorded
    msgs = [m.messageText for m in builder.messages]
    assert any("aborted after" in m for m in msgs), "Abort message was not logged"


def test_processing_context_failure(builder):
    class TestError(Exception):
        pass

    with pytest.raises(TestError):
        with builder.processingContext("Fail Test"):
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
    assert serialized["m"] == []
    rebuilt = ConversionResults.fromDict(serialized)
    assert rebuilt.messages == []


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


def test_console_processing_context_success(builder_with_console):
    with builder_with_console.processingContext("Simple Task") as ctx:
        sleep(0.001)  # simulate work
        ctx.mark("Step 2")

    assert ctx.succeeded
    messages = builder_with_console.getMessages(
        wantedMessageTypes={MessageType.Progress}
    )
    assert any('Finished: "Simple Task"' in m.messageText for m in messages)
    assert any("Starting: [Step 2]" in m.messageText for m in messages)


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
    with pytest.raises(ValueError):
        with builder_with_console.processingContext("Fails Hard") as ctx:
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
