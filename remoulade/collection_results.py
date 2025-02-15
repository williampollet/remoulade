# This file is a part of Remoulade.
#
# Copyright (C) 2017,2018 WIREMIND SAS <dev@wiremind.fr>
#
# Remoulade is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at
# your option) any later version.
#
# Remoulade is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
# License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import annotations

import time
from collections import deque
from typing import Any, Generator, Generic, Iterable, List, Optional, TypeVar, Union, cast, overload

from typing_extensions import Literal

from remoulade.results.errors import ErrorStored

from .broker import get_broker
from .result import Result
from .results import ResultBackend

ResultT = TypeVar("ResultT", bound=Union[Result, "CollectionResults"])
R = TypeVar("R")


class CollectionResults(Generic[ResultT]):
    """Result of a group or pipeline, having result related methods

    Parameters:
      children(List[Result|CollectionResults]): A sequence of results of messages, groups or pipelines.
    """

    def __init__(self, children: "Iterable[ResultT]") -> None:
        self.children = list(children)

    def __len__(self) -> int:
        return len(self.message_ids)

    @classmethod
    def from_message_ids(cls, message_ids: Iterable[str]) -> "CollectionResults[Any]":
        children = []
        for message_id in message_ids:
            # it's a pipeline
            if isinstance(message_id, list):
                last = message_id[-1]
                # it's a group
                if isinstance(last, list):
                    child = cls.from_message_ids(last)
                else:
                    child = Result(message_id=last)
            else:
                child = Result[Any](message_id=message_id)
            children.append(child)
        return CollectionResults(children)

    @property
    def completed(self) -> bool:
        """Returns True when all the jobs have been
        completed.  Actors that don't store results are not counted,
        meaning this may be inaccurate if all or some of your actors
        don't store results.

        Raises:
          RuntimeError: If your broker doesn't have a result backend
            set up.
        """
        return self.completed_count == len(self)

    @property
    def message_ids(self) -> List[str]:
        message_ids: List[str] = []
        for child in self.children:
            if isinstance(child, CollectionResults):
                message_ids += child.message_ids
            else:
                message_ids += [cast(Result[Any], child).message_id]
        return message_ids

    @property
    def _backend(self) -> ResultBackend:
        broker = get_broker()
        return broker.get_result_backend()

    @property
    def completed_count(self) -> int:
        """Returns the total number of jobs that have been completed.
        Actors that don't store results are not counted, meaning this
        may be inaccurate if all or some of your actors don't store
        results.

        Raises:
          RuntimeError: If your broker doesn't have a result backend
            set up.

        Returns:
          int: The total number of results.
        """
        # we could use message.completed here, but we just want to make 1 call to get_status
        return self._backend.get_status(self.message_ids)

    @overload
    def get(
        self: CollectionResults[Result[R]],
        *,
        block: bool = False,
        timeout: Optional[int] = None,
        raise_on_error: Literal[True] = True,
        forget: bool = False,
    ) -> Generator[R, None, None]:
        ...

    @overload
    def get(
        self: CollectionResults[Result[R]],
        *,
        block: bool = False,
        timeout: Optional[int] = None,
        raise_on_error: bool = True,
        forget: bool = False,
    ) -> Generator[Union[R, ErrorStored], None, None]:
        ...

    @overload
    def get(
        self, *, block: bool = False, timeout: Optional[int] = None, raise_on_error: bool = True, forget: bool = False
    ) -> Generator[Any, None, None]:
        ...

    def get(
        self, *, block: bool = False, timeout: Optional[int] = None, raise_on_error: bool = True, forget: bool = False
    ) -> Generator[Any, None, None]:
        """Get the results of each job in the collection.

        Parameters:
          block(bool): Whether or not to block until the results are stored.
          timeout(int): The maximum amount of time, in milliseconds,
            to wait for results when block is True.  Defaults to 10
            seconds.
          raise_on_error(bool): raise an error if the result stored in
            an error
          forget(bool): if true the result is discarded from the result
            backend

        Raises:
          ResultMissing: When block is False and the results aren't set.
          ResultTimeout: When waiting for results times out.
          ErrorStored: When the result is an error and raise_on_error is True

        Returns:
          A result generator.
        """
        deadline = None
        if timeout:
            deadline = time.monotonic() + timeout / 1000

        message_ids: List[str] = []
        for child in self.children:
            if deadline:
                timeout = max(0, int((deadline - time.monotonic()) * 1000))

            if isinstance(child, CollectionResults):
                # before yielding collection result, get results once for all normal results before
                if message_ids:
                    yield from self._backend.get_results(
                        message_ids, block=block, timeout=timeout, raise_on_error=raise_on_error, forget=forget
                    )
                    message_ids = []

                yield list(child.get(block=block, timeout=timeout, raise_on_error=raise_on_error, forget=forget))
            else:
                message_ids.append(cast(Result[Any], child).message_id)

        if message_ids:
            yield from self._backend.get_results(
                message_ids, block=block, timeout=timeout, raise_on_error=raise_on_error, forget=forget
            )

    def wait(self, *, timeout: Optional[int] = None, raise_on_error: bool = True, forget: bool = False) -> None:
        """Block until all the jobs in the collection have finished or
        until the timeout expires.

        Parameters:
          timeout(int): The maximum amount of time, in ms, to wait.
            Defaults to 10 seconds.
          raise_on_error(bool): raise an error if one of the result stored in
            an error
          forget(bool): if true the result is discarded from the result
            backend
        """
        iterator = self.get(block=True, timeout=timeout, raise_on_error=raise_on_error, forget=forget)
        # Consume the iterator (https://docs.python.org/3/library/itertools.html#itertools-recipes)
        deque(iterator, maxlen=0)
