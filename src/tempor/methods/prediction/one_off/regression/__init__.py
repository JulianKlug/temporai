"""One-off regression estimators."""

import abc
from typing import Any, Tuple

import numpy as np
import pydantic
from typing_extensions import Self

import tempor.methods.core as methods_core
from tempor.core import plugins, pydantic_utils
from tempor.data import dataset, samples


def check_data_class(data: Any) -> None:
    """Check if data is a one-off prediction dataset.

    Args:
        data (Any): The data.

    Raises:
        TypeError: If data is not a one-off prediction dataset.
    """
    if not isinstance(data, dataset.OneOffPredictionDataset):
        raise TypeError(
            "Expected `data` passed to a one-off regression estimator to be "
            f"`{dataset.OneOffPredictionDataset.__name__}` but was {type(data)}"
        )


class BaseOneOffRegressor(methods_core.BasePredictor):
    def __init__(self, **params: Any) -> None:  # pylint: disable=useless-super-delegation
        """Base class for one-off regression estimators.

        Args:
            **params (Any):
                Parameters as defined in :class:`BasePredictorParams`.
        """
        super().__init__(**params)

    def fit(self, data: dataset.BaseDataset, *args: Any, **kwargs: Any) -> Self:  # noqa: D102
        check_data_class(data)
        super().fit(data, *args, **kwargs)
        return self

    @pydantic_utils.validate_arguments(config=pydantic.ConfigDict(arbitrary_types_allowed=True))
    def predict(
        self,
        data: dataset.PredictiveDataset,
        *args: Any,
        **kwargs: Any,
    ) -> samples.StaticSamples:  # noqa: D102
        check_data_class(data)
        return super().predict(data, *args, **kwargs)

    @abc.abstractmethod
    def _predict(
        self, data: dataset.PredictiveDataset, *args: Any, **kwargs: Any
    ) -> samples.StaticSamples:  # pragma: no cover
        ...

    def _unpack_dataset(self, data: dataset.BaseDataset) -> Tuple:
        temporal = data.time_series.numpy()
        observation_times = data.time_series.time_indexes()
        if data.predictive is not None and data.predictive.targets is not None:
            outcome = data.predictive.targets.numpy()
        else:
            outcome = np.zeros((len(temporal), 0))

        if data.static is not None:
            static = data.static.numpy()
        else:
            static = np.zeros((len(temporal), 0))

        return static, temporal, observation_times, outcome


plugins.register_plugin_category("prediction.one_off.regression", BaseOneOffRegressor)

plugins.importing.import_plugins(__file__)

__all__ = [  # pyright: ignore
    *plugins.importing.gather_modules_names(__file__),
    "BaseOneOffRegressor",
]
