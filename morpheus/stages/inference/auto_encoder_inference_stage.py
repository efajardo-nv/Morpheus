# Copyright (c) 2021-2022, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import typing
from functools import partial

import cupy as cp
import numpy as np
import pandas as pd
import srf
from srf.core import operators as ops

from morpheus.config import Config
from morpheus.messages import MultiInferenceMessage
from morpheus.messages import MultiResponseProbsMessage
from morpheus.messages import ResponseMemory
from morpheus.messages import ResponseMemoryAE
from morpheus.messages import ResponseMemoryProbs
from morpheus.messages.multi_inference_ae_message import MultiInferenceAEMessage
from morpheus.pipeline.stream_pair import StreamPair
from morpheus.stages.inference.inference_stage import InferenceStage
from morpheus.stages.inference.inference_stage import InferenceWorker
from morpheus.utils.producer_consumer_queue import ProducerConsumerQueue


class _AutoEncoderInferenceWorker(InferenceWorker):

    def __init__(self, inf_queue: ProducerConsumerQueue, c: Config):
        super().__init__(inf_queue)

        self._max_batch_size = c.model_max_batch_size
        self._seq_length = c.feature_length

        self._feature_columns = c.ae.feature_columns

    def init(self):

        pass

    def build_output_message(self, x: MultiInferenceAEMessage) -> MultiResponseProbsMessage:
        """
        Create initial inference response message with result values initialized to zero. Results will be
        set in message as each inference batch is processed.

        Parameters
        ----------
        x : `morpheus.pipeline.messages.MultiInferenceAEMessage`
            Batch of autoencoder inference messages.

        Returns
        -------
        `morpheus.pipeline.messagesMultiResponseAEMessage`
            Response message with autoencoder results calculated from inference results.
        """

        dims = self.calc_output_dims(x)
        output_dims = (x.mess_count, *dims[1:])

        memory = ResponseMemoryProbs(count=output_dims[0], probs=cp.zeros(output_dims))

        # Override the default to return the response AE
        output_message = MultiResponseProbsMessage(meta=x.meta,
                                                mess_offset=x.mess_offset,
                                                mess_count=x.mess_count,
                                                memory=memory,
                                                offset=x.offset,
                                                count=x.count)
        return output_message

    def calc_output_dims(self, x: MultiInferenceAEMessage) -> typing.Tuple:

        # reconstruction loss and zscore
        return (x.count, 2)

    def process(self, batch: MultiInferenceAEMessage, cb: typing.Callable[[ResponseMemory], None]):
        """
        This function processes inference batch by using batch's model to calculate anomaly scores
        and adding results to response.

        Parameters
        ----------
        batch : `morpheus.pipeline.messages.MultiInferenceMessage`
            Batch of inference messages.
        cb : typing.Callable[[`morpheus.pipeline.messages.ResponseMemory`], None]
            Inference callback.

        """
        data = batch.get_meta(batch.meta.df.columns.intersection(self._feature_columns))

        explain_df = pd.DataFrame(np.empty((batch.count, 3), dtype=object),
                                  columns=["num_col_max_loss", "bin_col_max_loss", "cat_col_max_loss"])
        if batch.model is not None:
            mse_loss, bce_loss, cce_loss, rloss_scores = batch.model.get_anomaly_score(data)
            num_names, cat_names, bin_names = batch.model.return_feature_names()
            vi_df = batch.model.get_variable_importance(num_names,
                                                        cat_names,
                                                        bin_names,
                                                        mse_loss,
                                                        bce_loss,
                                                        cce_loss,
                                                        data)
            for col in vi_df.columns:
                explain_df[col] = vi_df[col]

            zscores = (rloss_scores - batch.train_scores_mean) / batch.train_scores_std
            rloss_scores = rloss_scores.reshape((batch.count, 1))
            zscores = np.absolute(zscores)
            zscores = zscores.reshape((batch.count, 1))
        else:
            rloss_scores = np.empty((batch.count, 1))
            rloss_scores[:] = np.NaN
            zscores = np.empty((batch.count, 1))
            zscores[:] = np.NaN

        ae_scores = np.concatenate((rloss_scores, zscores), axis=1)

        ae_scores = cp.asarray(ae_scores)

        mem = ResponseMemoryAE(count=batch.count, probs=ae_scores)

        mem.explain_df = explain_df

        cb(mem)


class AutoEncoderInferenceStage(InferenceStage):
    """
    Inference stage for the AE pipeline.
    """

    def __init__(self, c: Config):
        super().__init__(c)

        self._config = c

    def _get_inference_worker(self, inf_queue: ProducerConsumerQueue) -> InferenceWorker:

        return _AutoEncoderInferenceWorker(inf_queue, self._config)

    @staticmethod
    def _convert_one_response(memory: ResponseMemory, inf: MultiInferenceMessage, res: ResponseMemoryAE):
        # Make sure we have a continuous list
        # assert inf.mess_offset == saved_offset + saved_count

        res.explain_df.index = range(inf.mess_offset, inf.mess_offset + inf.mess_count)
        for col in res.explain_df.columns:
            inf.set_meta(col, res.explain_df[col])

        probs = memory.get_output("probs")

        # Two scenarios:
        if (inf.mess_count == inf.count):
            # In message and out message have same count. Just use probs as is
            probs[inf.offset:inf.count + inf.offset, :] = res.probs
        else:
            assert inf.count == res.count

            mess_ids = inf.seq_ids[:, 0].get().tolist()

            # Out message has more reponses, so we have to do key based blending of probs
            for i, idx in enumerate(mess_ids):
                probs[idx, :] = cp.maximum(probs[idx, :], res.probs[i, :])

        return MultiResponseProbsMessage(meta=inf.meta,
                                      mess_offset=inf.mess_offset,
                                      mess_count=inf.mess_count,
                                      memory=memory,
                                      offset=inf.offset,
                                      count=inf.count)

