import threading
from sys import stderr
from typing import Any, Callable, Dict, Optional, Tuple, Union

from pylspclient import lsp_structs
from pylspclient.json_rpc_endpoint import JsonRpcEndpoint


class LspEndpoint(threading.Thread):
    event_dict: Dict[int, threading.Condition]
    response_dict: Dict[int, Tuple[Any, Any]]

    def __init__(
        self,
        json_rpc_endpoint: JsonRpcEndpoint,
        method_callbacks: Dict[str, Callable[..., Any]] = {},
        notify_callbacks: Dict[str, Callable[..., Any]] = {},
        timeout: int = 2,
    ):
        threading.Thread.__init__(self)
        self.json_rpc_endpoint = json_rpc_endpoint
        self.notify_callbacks = notify_callbacks
        self.method_callbacks = method_callbacks
        self.event_dict = {}
        self.response_dict = {}
        self.next_id = 0
        self._timeout = timeout
        self.shutdown_flag = False

    def handle_result(self, rpc_id: int, result: Any, error: Any):
        self.response_dict[rpc_id] = (result, error)
        cond = self.event_dict[rpc_id]
        cond.acquire()
        cond.notify()
        cond.release()

    def stop(self):
        self.shutdown_flag = True

    def run(self):
        while not self.shutdown_flag:
            try:
                jsonrpc_message = self.json_rpc_endpoint.recv_response()
                if jsonrpc_message is None:
                    print("server quit", file=stderr)
                    break
                method = jsonrpc_message.get("method")
                result = jsonrpc_message.get("result")
                error = jsonrpc_message.get("error")
                rpc_id = jsonrpc_message.get("id")
                params = jsonrpc_message.get("params")
                try:
                    if method:
                        if rpc_id:
                            # a call for method
                            if method not in self.method_callbacks:
                                raise lsp_structs.ResponseError(
                                    lsp_structs.ErrorCodes.MethodNotFound,
                                    "Method not found: {method}".format(method=method),
                                )
                            result = self.method_callbacks[method](params)
                            self.send_response(rpc_id, result, None)
                        else:
                            # a call for notify
                            if method not in self.notify_callbacks:
                                # Have nothing to do with this.
                                print(
                                    "Notify method not found: {method}: {param}.".format(
                                        method=method, param=params
                                    ),
                                    file=stderr,
                                )
                            else:
                                self.notify_callbacks[method](params)
                    else:
                        self.handle_result(rpc_id, result, error)
                except lsp_structs.ResponseError as e:
                    self.send_response(rpc_id, None, e)
            except lsp_structs.ResponseError as e:
                ## self.send_response(rpc_id, None, e)
                pass

    def send_response(self, id: int, result: Any, error: Any):
        message_dict = {}
        message_dict["jsonrpc"] = "2.0"
        message_dict["id"] = id
        if result:
            message_dict["result"] = result
        if error:
            message_dict["error"] = error
        self.json_rpc_endpoint.send_request(message_dict)

    def send_message(
        self,
        method_name: str,
        params: Dict[str, Union[str, int, None]],
        id: Optional[int] = None,
    ):
        message_dict = {}
        message_dict["jsonrpc"] = "2.0"
        if id is not None:
            message_dict["id"] = id
        message_dict["method"] = method_name
        message_dict["params"] = params
        self.json_rpc_endpoint.send_request(message_dict)

    def call_method(self, method_name: str, **kwargs: Any):
        current_id = self.next_id
        self.next_id += 1
        cond = threading.Condition()
        self.event_dict[current_id] = cond

        cond.acquire()
        self.send_message(method_name, kwargs, current_id)
        if self.shutdown_flag:
            return None

        if not cond.wait(timeout=self._timeout):
            raise TimeoutError()
        cond.release()

        self.event_dict.pop(current_id)
        result, error = self.response_dict.pop(current_id)
        if error:
            raise lsp_structs.ResponseError(
                error.get("code"), error.get("message"), error.get("data")
            )
        return result

    def send_notification(self, method_name: str, **kwargs: Any):
        self.send_message(method_name, kwargs)
