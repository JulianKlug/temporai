import copy
from time import time
from typing import Any, Dict, List, Sequence, Union, cast

import numpy as np
import pandas as pd
import pydantic
from scipy.stats import iqr
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    f1_score,
    matthews_corrcoef,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import KFold, StratifiedKFold
from typing_extensions import Literal, get_args

from tempor.benchmarks.utils import generate_score, print_score
from tempor.data import dataset
from tempor.log import logger as log
from tempor.models.utils import enable_reproducibility

from .utils import evaluate_auc_multiclass

ClassifierSupportedMetric = Literal[
    "aucroc",
    "aucprc",
    "accuracy",
    "f1_score_micro",
    "f1_score_macro",
    "f1_score_weighted",
    "kappa",
    "kappa_quadratic",
    "precision_micro",
    "precision_macro",
    "precision_weighted",
    "recall_micro",
    "recall_macro",
    "recall_weighted",
    "mcc",
]
"""Evaluation metrics supported in the classification task setting.

Possible values:
    - ``"aucroc"``:
        The Area Under the Receiver Operating Characteristic Curve (ROC AUC) from prediction scores.
    - ``"aucprc"``:
        The average precision summarizes a precision-recall curve as the weighted mean of precisions achieved at each
        threshold, with the increase in recall from the previous threshold used as the weight.
    - ``"accuracy"``:
        Accuracy classification score.
    - ``"f1_score_micro"``:
        F1 score is a harmonic mean of the precision and recall. This version uses the ``"micro"`` average:
        calculate metrics globally by counting the total true positives, false negatives and false positives.
    - ``"f1_score_macro"``:
        F1 score is a harmonic mean of the precision and recall. This version uses the ``"macro"`` average:
        calculate metrics for each label, and find their unweighted mean. This does not take label imbalance into
        account.
    - ``"f1_score_weighted"``:
        F1 score is a harmonic mean of the precision and recall. This version uses the "weighted" average:
        Calculate metrics for each label, and find their average weighted by support
        (the number of true instances for each label).
    - ``"kappa"``, ``"kappa_quadratic"``:
        Computes Cohen's kappa, a score that expresses the level of agreement between two annotators on a
        classification problem.
    - ``"precision_micro"``:
        Precision is defined as the number of true positives over the number of true positives plus the number of false
        positives. This version(micro) calculates metrics globally by counting the total true positives.
    - ``"precision_macro"``:
        Precision is defined as the number of true positives over the number of true positives plus the number of
        false positives. This version (macro) calculates metrics for each label, and finds their unweighted mean.
    - ``"precision_weighted"``:
        Precision is defined as the number of true positives over the number of true positives plus the number of
        false positives. This version (weighted) calculates metrics for each label, and find their average weighted
        by support.
    - ``"recall_micro"``:
        Recall is defined as the number of true positives over the number of true positives plus the number of false
        negatives. This version (micro) calculates metrics globally by counting the total true positives.
    - ``"recall_macro"``:
        Recall is defined as the number of true positives over the number of true positives plus the number of false
        negatives. This version (macro) calculates metrics for each label, and finds their unweighted mean.
    - ``"recall_weighted"``:
        Recall is defined as the number of true positives over the number of true positives plus the number of false
        negatives. This version(weighted) calculates metrics for each label, and find their average weighted by support.
    - ``"mcc"``:
        The Matthews Correlation Coefficient is used in machine learning as a measure of the quality of binary and
        multiclass classifications. It takes into account true and false positives and negatives and is generally
        regarded as a balanced measure which can be used even if the classes are of very different sizes.
"""

RegressionSupportedMetric = Literal[
    "mse",
    "mae",
    "r2",
]
"""Evaluation metrics supported in the regression task setting.

Possible values:
    - ``"r2"``:
        R^2 (coefficient of determination) regression score function.
    - ``"mse"``:
        Mean squared error regression loss.
    - ``"mae"``:
        Mean absolute error regression loss.
"""

OutputMetric = Literal[
    "min",
    "max",
    "mean",
    "stddev",
    "median",
    "iqr",
    "rounds",
    "errors",
    "durations",
]
"""The metric evaluation output statistics / other information about the evaluation cross-validation runs.

Possible values:
    - ``"min"``:
        The mix score of the metric
    - ``"max"``:
        The max score of the metric
    - ``"mean"``:
        The mean score of the metric
    - ``"stddev"``:
        The stddev score of the metric
    - ``"median"``:
        The median score of the metric
    - ``"iqr"``:
        The interquartile range of the metric
    - ``"rounds"``:
        Number of folds
    - ``"errors"``:
        Number of errors encountered
    - ``"durations"``:
        Average duration for the fold evaluation.
"""

classifier_supported_metrics = get_args(ClassifierSupportedMetric)
"""A tuple of all possible values of :obj:`~tempor.benchmarks.evaluation.ClassifierSupportedMetric`."""

regression_supported_metrics = get_args(RegressionSupportedMetric)
"""A tuple of all possible values of :obj:`~tempor.benchmarks.evaluation.RegressionSupportedMetric`."""

output_metrics = get_args(OutputMetric)
"""A tuple of all possible values of :obj:`~tempor.benchmarks.evaluation.OutputMetric`."""


class _InternalScores(pydantic.BaseModel):
    metrics: Dict[str, np.ndarray] = {}  # np.ndarray expected to be 1D, contain floats.
    errors: List[int] = []
    durations: List[float] = []

    class Config:
        arbitrary_types_allowed = True


@pydantic.validate_arguments(config=dict(arbitrary_types_allowed=True))
def _postprocess_results(results: _InternalScores) -> pd.DataFrame:
    output = pd.DataFrame([], columns=output_metrics)

    for metric in results.metrics:
        values = results.metrics[metric]
        errors = np.sum(results.errors)
        durations = print_score(generate_score(np.asarray(results.durations)))

        score_min = np.min(values)
        score_max = np.max(values)
        score_mean = np.mean(values)
        score_median = np.median(values)
        score_stddev = np.std(values)
        score_iqr = iqr(values)
        score_rounds = len(values)

        output = pd.concat(
            [
                output,
                pd.DataFrame(
                    [
                        [
                            score_min,
                            score_max,
                            score_mean,
                            score_stddev,
                            score_median,
                            score_iqr,
                            score_rounds,
                            errors,
                            durations,
                        ]
                    ],
                    columns=output_metrics,
                    index=[metric],
                ),
            ],
        )

    return output


class ClassifierMetrics:
    @pydantic.validate_arguments
    def __init__(
        self,
        metric: Union[ClassifierSupportedMetric, Sequence[ClassifierSupportedMetric]] = classifier_supported_metrics,
    ) -> None:
        """Helper class for evaluating the performance of the classifier.

        Args:
            metric (Union[ClassifierSupportedMetric, Sequence[ClassifierSupportedMetric]], optional):
                The type of metric(s) to use for evaluation.
                A string (one of :obj:`~tempor.benchmarks.evaluation.ClassifierSupportedMetric`) or a sequence of such.
                Defaults to :obj:`~tempor.benchmarks.evaluation.classifier_supported_metrics`.
        """
        self.metrics: Union[ClassifierSupportedMetric, Sequence[ClassifierSupportedMetric]]
        if isinstance(metric, str):
            self.metrics = [cast(ClassifierSupportedMetric, metric)]
        else:
            self.metrics = metric

    def get_metric(self) -> Union[ClassifierSupportedMetric, Sequence[ClassifierSupportedMetric]]:
        return self.metrics

    def score_proba(self, y_test: np.ndarray, y_pred_proba: np.ndarray) -> Dict[str, float]:
        if y_test is None or y_pred_proba is None:
            raise RuntimeError("Invalid input for score_proba")

        results = {}
        y_pred = np.argmax(np.asarray(y_pred_proba), axis=1)
        for metric in self.metrics:
            if metric == "aucprc":
                results[metric] = self.average_precision_score(y_test, y_pred_proba)
            elif metric == "aucroc":
                results[metric] = self.roc_auc_score(y_test, y_pred_proba)
            elif metric == "accuracy":
                results[metric] = accuracy_score(y_test, y_pred)
            elif metric == "f1_score_micro":
                results[metric] = f1_score(
                    y_test,
                    y_pred,
                    average="micro",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "f1_score_macro":
                results[metric] = f1_score(
                    y_test,
                    y_pred,
                    average="macro",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "f1_score_weighted":
                results[metric] = f1_score(
                    y_test,
                    y_pred,
                    average="weighted",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "kappa":
                results[metric] = cohen_kappa_score(y_test, y_pred)
            elif metric == "kappa_quadratic":
                results[metric] = cohen_kappa_score(y_test, y_pred, weights="quadratic")
            elif metric == "recall_micro":
                results[metric] = recall_score(
                    y_test,
                    y_pred,
                    average="micro",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "recall_macro":
                results[metric] = recall_score(
                    y_test,
                    y_pred,
                    average="macro",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "recall_weighted":
                results[metric] = recall_score(
                    y_test,
                    y_pred,
                    average="weighted",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "precision_micro":
                results[metric] = precision_score(
                    y_test,
                    y_pred,
                    average="micro",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "precision_macro":
                results[metric] = precision_score(
                    y_test,
                    y_pred,
                    average="macro",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "precision_weighted":
                results[metric] = precision_score(
                    y_test,
                    y_pred,
                    average="weighted",
                    zero_division=0,  # pyright: ignore
                )
            elif metric == "mcc":
                results[metric] = matthews_corrcoef(y_test, y_pred)
            else:
                raise ValueError(f"invalid metric {metric}")

        log.debug(f"evaluate_classifier: {results}")
        return results

    def roc_auc_score(self, y_test: np.ndarray, y_pred_proba: np.ndarray) -> float:
        return evaluate_auc_multiclass(y_test, y_pred_proba)[0]

    def average_precision_score(self, y_test: np.ndarray, y_pred_proba: np.ndarray) -> float:

        return evaluate_auc_multiclass(y_test, y_pred_proba)[1]


@pydantic.validate_arguments(config=dict(arbitrary_types_allowed=True))
def evaluate_classifier(  # pylint: disable=unused-argument
    estimator: Any,
    data: dataset.Dataset,
    *args: Any,
    n_splits: int = 3,
    random_state: int = 0,
    **kwargs: Any,
) -> pd.DataFrame:
    """Helper for evaluating classifiers.

    Args:
        estimator (Any):
            Baseline model to evaluate - must be unfitted.
        data (dataset.Dataset):
            The dataset.
        n_splits (int, optional):
            Cross-validation folds. Defaults to ``3``.
        random_state (int, optional):
            Random state. Defaults to ``0``.

    Returns:
        pd.DataFrame:
            DataFrame containing the results.

            The columns of the dataframe contain details about the cross-validation repeats: one column for each
            :obj:`~tempor.benchmarks.evaluation.OutputMetric`.

            The index of the dataframe contains all the metrics evaluated: all of
            :obj:`~tempor.benchmarks.evaluation.ClassifierSupportedMetric`.
    """

    if n_splits < 2 or not isinstance(n_splits, int):
        raise ValueError("n_splits must be an integer >= 2")
    enable_reproducibility(random_state)

    results = _InternalScores()
    evaluator = ClassifierMetrics()
    for metric in classifier_supported_metrics:
        results.metrics[metric] = np.zeros(n_splits)

    splitter = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    if data.predictive is None:
        raise ValueError("No targets to use for train/test")

    labels = data.predictive.targets.numpy().squeeze()
    if len(labels.shape) > 1:
        raise ValueError("Classifier evaluation expects 1D output")

    indx = 0
    for train_data, test_data in data.split(splitter=splitter, y=labels):
        model = copy.deepcopy(estimator)
        start = time()
        try:
            model.fit(train_data)

            if test_data.predictive is None:
                raise ValueError("No targets to use for testing")

            test_labels = test_data.predictive.targets.numpy()
            preds = model.predict_proba(test_data).numpy()

            scores = evaluator.score_proba(test_labels, preds)
            for metric in scores:
                results.metrics[metric][indx] = scores[metric]
            results.errors.append(0)
        except BaseException as e:  # pylint: disable=broad-except
            log.error(f"Evaluation failed: {e}")
            results.errors.append(1)

        results.durations.append(time() - start)
        indx += 1

    return _postprocess_results(results)


@pydantic.validate_arguments(config=dict(arbitrary_types_allowed=True))
def evaluate_regressor(  # pylint: disable=unused-argument
    estimator: Any,
    data: dataset.Dataset,
    *args: Any,
    n_splits: int = 3,
    random_state: int = 0,
    **kwargs: Any,
) -> pd.DataFrame:
    """Helper for evaluating regression tasks.

    Args:
        estimator (Any):
            Baseline model to evaluate - must be unfitted.
        data (dataset.Dataset):
            The dataset.
        n_splits (int, optional):
            Cross-validation folds. Defaults to ``3``.
        random_state (int, optional):
            Random state. Defaults to ``0``.

    Returns:
        pd.DataFrame:
            DataFrame containing the results.

            The columns of the dataframe contain details about the cross-validation repeats: one column for each
            :obj:`~tempor.benchmarks.evaluation.OutputMetric`.

            The index of the dataframe contains all the metrics evaluated: all of
            :obj:`~tempor.benchmarks.evaluation.RegressionSupportedMetric`.
    """
    if n_splits < 2 or not isinstance(n_splits, int):
        raise ValueError("n_splits must be an integer >= 2")

    enable_reproducibility(random_state)
    metrics = regression_supported_metrics

    results = _InternalScores()
    for metric in metrics:
        results.metrics[metric] = np.zeros(n_splits)

    indx = 0

    splitter = KFold(n_splits=n_splits, shuffle=True, random_state=random_state)

    for train_data, test_data in data.split(splitter=splitter):
        model = copy.deepcopy(estimator)
        start = time()
        try:
            model.fit(train_data)

            if test_data.predictive is None:
                raise ValueError("Missing targets for evaluation")

            targets = test_data.predictive.targets.numpy().squeeze()
            preds = model.predict(test_data).numpy().squeeze()

            results.metrics["mse"][indx] = mean_squared_error(targets, preds)
            results.metrics["mae"][indx] = mean_absolute_error(targets, preds)
            results.metrics["r2"][indx] = r2_score(targets, preds)
            results.errors.append(0)
        except BaseException as e:  # pylint: disable=broad-except
            log.error(f"Regression evaluation failed: {e}")
            results.errors.append(1)

        results.durations.append(time() - start)
        indx += 1

    return _postprocess_results(results)