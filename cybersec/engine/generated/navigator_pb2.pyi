from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CommandRequest(_message.Message):
    __slots__ = ("session_id", "command_id", "text", "query", "load_dataset", "set_param", "export")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    LOAD_DATASET_FIELD_NUMBER: _ClassVar[int]
    SET_PARAM_FIELD_NUMBER: _ClassVar[int]
    EXPORT_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    command_id: str
    text: str
    query: QueryCommand
    load_dataset: LoadDatasetCommand
    set_param: SetParamCommand
    export: ExportCommand
    def __init__(self, session_id: _Optional[str] = ..., command_id: _Optional[str] = ..., text: _Optional[str] = ..., query: _Optional[_Union[QueryCommand, _Mapping]] = ..., load_dataset: _Optional[_Union[LoadDatasetCommand, _Mapping]] = ..., set_param: _Optional[_Union[SetParamCommand, _Mapping]] = ..., export: _Optional[_Union[ExportCommand, _Mapping]] = ...) -> None: ...

class QueryCommand(_message.Message):
    __slots__ = ("expression", "time_range", "limit")
    EXPRESSION_FIELD_NUMBER: _ClassVar[int]
    TIME_RANGE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    expression: str
    time_range: str
    limit: int
    def __init__(self, expression: _Optional[str] = ..., time_range: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class LoadDatasetCommand(_message.Message):
    __slots__ = ("dataset_name", "data_path")
    DATASET_NAME_FIELD_NUMBER: _ClassVar[int]
    DATA_PATH_FIELD_NUMBER: _ClassVar[int]
    dataset_name: str
    data_path: str
    def __init__(self, dataset_name: _Optional[str] = ..., data_path: _Optional[str] = ...) -> None: ...

class SetParamCommand(_message.Message):
    __slots__ = ("param_name", "value")
    PARAM_NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    param_name: str
    value: str
    def __init__(self, param_name: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class ExportCommand(_message.Message):
    __slots__ = ("format", "destination", "expression")
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    EXPRESSION_FIELD_NUMBER: _ClassVar[int]
    format: str
    destination: str
    expression: str
    def __init__(self, format: _Optional[str] = ..., destination: _Optional[str] = ..., expression: _Optional[str] = ...) -> None: ...

class CancelRequest(_message.Message):
    __slots__ = ("session_id", "command_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    command_id: str
    def __init__(self, session_id: _Optional[str] = ..., command_id: _Optional[str] = ...) -> None: ...

class EngineEvent(_message.Message):
    __slots__ = ("event_id", "command_id", "timestamp_ms", "text_output", "progress", "error", "param_update", "data_ready", "render_hint", "command_complete", "status_snapshot", "data_result")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    TEXT_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    PARAM_UPDATE_FIELD_NUMBER: _ClassVar[int]
    DATA_READY_FIELD_NUMBER: _ClassVar[int]
    RENDER_HINT_FIELD_NUMBER: _ClassVar[int]
    COMMAND_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    STATUS_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    DATA_RESULT_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    command_id: str
    timestamp_ms: int
    text_output: TextOutput
    progress: ProgressUpdate
    error: ErrorOutput
    param_update: ParamUpdate
    data_ready: DataReady
    render_hint: RenderHint
    command_complete: CommandComplete
    status_snapshot: StatusSnapshot
    data_result: DataResult
    def __init__(self, event_id: _Optional[str] = ..., command_id: _Optional[str] = ..., timestamp_ms: _Optional[int] = ..., text_output: _Optional[_Union[TextOutput, _Mapping]] = ..., progress: _Optional[_Union[ProgressUpdate, _Mapping]] = ..., error: _Optional[_Union[ErrorOutput, _Mapping]] = ..., param_update: _Optional[_Union[ParamUpdate, _Mapping]] = ..., data_ready: _Optional[_Union[DataReady, _Mapping]] = ..., render_hint: _Optional[_Union[RenderHint, _Mapping]] = ..., command_complete: _Optional[_Union[CommandComplete, _Mapping]] = ..., status_snapshot: _Optional[_Union[StatusSnapshot, _Mapping]] = ..., data_result: _Optional[_Union[DataResult, _Mapping]] = ...) -> None: ...

class TextOutput(_message.Message):
    __slots__ = ("text", "style")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    STYLE_FIELD_NUMBER: _ClassVar[int]
    text: str
    style: str
    def __init__(self, text: _Optional[str] = ..., style: _Optional[str] = ...) -> None: ...

class ProgressUpdate(_message.Message):
    __slots__ = ("message", "fraction", "eta_seconds")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    FRACTION_FIELD_NUMBER: _ClassVar[int]
    ETA_SECONDS_FIELD_NUMBER: _ClassVar[int]
    message: str
    fraction: float
    eta_seconds: int
    def __init__(self, message: _Optional[str] = ..., fraction: _Optional[float] = ..., eta_seconds: _Optional[int] = ...) -> None: ...

class ErrorOutput(_message.Message):
    __slots__ = ("message", "code", "suggestion")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    SUGGESTION_FIELD_NUMBER: _ClassVar[int]
    message: str
    code: str
    suggestion: str
    def __init__(self, message: _Optional[str] = ..., code: _Optional[str] = ..., suggestion: _Optional[str] = ...) -> None: ...

class ParamUpdate(_message.Message):
    __slots__ = ("param_name", "value", "source")
    PARAM_NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    param_name: str
    value: str
    source: str
    def __init__(self, param_name: _Optional[str] = ..., value: _Optional[str] = ..., source: _Optional[str] = ...) -> None: ...

class DataReady(_message.Message):
    __slots__ = ("dataset_name", "partitions", "total_rows_estimate", "data_path")
    DATASET_NAME_FIELD_NUMBER: _ClassVar[int]
    PARTITIONS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ROWS_ESTIMATE_FIELD_NUMBER: _ClassVar[int]
    DATA_PATH_FIELD_NUMBER: _ClassVar[int]
    dataset_name: str
    partitions: int
    total_rows_estimate: int
    data_path: str
    def __init__(self, dataset_name: _Optional[str] = ..., partitions: _Optional[int] = ..., total_rows_estimate: _Optional[int] = ..., data_path: _Optional[str] = ...) -> None: ...

class RenderHint(_message.Message):
    __slots__ = ("action", "x_min", "x_max", "y_min", "y_max")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    X_MIN_FIELD_NUMBER: _ClassVar[int]
    X_MAX_FIELD_NUMBER: _ClassVar[int]
    Y_MIN_FIELD_NUMBER: _ClassVar[int]
    Y_MAX_FIELD_NUMBER: _ClassVar[int]
    action: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    def __init__(self, action: _Optional[str] = ..., x_min: _Optional[float] = ..., x_max: _Optional[float] = ..., y_min: _Optional[float] = ..., y_max: _Optional[float] = ...) -> None: ...

class CommandComplete(_message.Message):
    __slots__ = ("status", "duration_ms", "summary")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    status: str
    duration_ms: int
    summary: str
    def __init__(self, status: _Optional[str] = ..., duration_ms: _Optional[int] = ..., summary: _Optional[str] = ...) -> None: ...

class StatusSnapshot(_message.Message):
    __slots__ = ("workers", "processing", "dask_connected", "current_dataset", "dataset_phase", "partitions", "extra")
    class ExtraEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKERS_FIELD_NUMBER: _ClassVar[int]
    PROCESSING_FIELD_NUMBER: _ClassVar[int]
    DASK_CONNECTED_FIELD_NUMBER: _ClassVar[int]
    CURRENT_DATASET_FIELD_NUMBER: _ClassVar[int]
    DATASET_PHASE_FIELD_NUMBER: _ClassVar[int]
    PARTITIONS_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    workers: int
    processing: int
    dask_connected: bool
    current_dataset: str
    dataset_phase: str
    partitions: int
    extra: _containers.ScalarMap[str, str]
    def __init__(self, workers: _Optional[int] = ..., processing: _Optional[int] = ..., dask_connected: bool = ..., current_dataset: _Optional[str] = ..., dataset_phase: _Optional[str] = ..., partitions: _Optional[int] = ..., extra: _Optional[_Mapping[str, str]] = ...) -> None: ...

class DataResult(_message.Message):
    __slots__ = ("format", "payload", "url", "row_count")
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    ROW_COUNT_FIELD_NUMBER: _ClassVar[int]
    format: str
    payload: bytes
    url: str
    row_count: int
    def __init__(self, format: _Optional[str] = ..., payload: _Optional[bytes] = ..., url: _Optional[str] = ..., row_count: _Optional[int] = ...) -> None: ...

class StatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class StatusResponse(_message.Message):
    __slots__ = ("snapshot", "server_time", "engine_version")
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    SERVER_TIME_FIELD_NUMBER: _ClassVar[int]
    ENGINE_VERSION_FIELD_NUMBER: _ClassVar[int]
    snapshot: StatusSnapshot
    server_time: _timestamp_pb2.Timestamp
    engine_version: str
    def __init__(self, snapshot: _Optional[_Union[StatusSnapshot, _Mapping]] = ..., server_time: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., engine_version: _Optional[str] = ...) -> None: ...

class SubscribeRequest(_message.Message):
    __slots__ = ("session_id", "replay_history", "replay_limit")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    REPLAY_HISTORY_FIELD_NUMBER: _ClassVar[int]
    REPLAY_LIMIT_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    replay_history: bool
    replay_limit: int
    def __init__(self, session_id: _Optional[str] = ..., replay_history: bool = ..., replay_limit: _Optional[int] = ...) -> None: ...
