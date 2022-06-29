<!--
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
-->

# Digital Fingerprinting Pipeline

We show here how to set up and run the DFP pipeline for three log types: CloudTrail, Duo and Azure. Each of these log types uses a built-in source stage that handles that specific data format. New source stages can be added to allow the DFP pipeline to process different log types. All stages after the source stages are identical across all log types but can be configured differently via pipeline or stage configuration options.

## Environment Setup

Follow the instructions [here](https://github.com/nv-morpheus/Morpheus/blob/branch-22.06/CONTRIBUTING.md) to set up your development environment in either a Docker container or conda environment.

## Morpheus CLI

DFP pipelines can be constructed and run using the Morpheus CLI command `morpheus run pipeline-ae ...`

Use `--help` to display information about the autoencoder pipeline command line options:

```
morpheus run pipeline-ae --help

Usage: morpheus run pipeline-ae [OPTIONS] COMMAND1 [ARGS]... [COMMAND2
                                [ARGS]...]...

  Configure and run the pipeline. To configure the pipeline, list the stages
  in the order that data should flow. The output of each stage will become the
  input for the next stage. For example, to read, classify and write to a
  file, the following stages could be used

  pipeline from-file --filename=my_dataset.json deserialize preprocess inf-triton --model_name=my_model
  --server_url=localhost:8001 filter --threshold=0.5 to-file --filename=classifications.json

  Pipelines must follow a few rules:
  1. Data must originate in a source stage. Current options are `from-file` or `from-kafka`
  2. A `deserialize` stage must be placed between the source stages and the rest of the pipeline
  3. Only one inference stage can be used. Zero is also fine
  4. The following stages must come after an inference stage: `add-class`, `filter`, `gen-viz`

Options:
  --columns_file FILE             [default: /my_data/gitrepos/efajardo-nv/Morp
                                  heus/morpheus/data/columns_ae_cloudtrail.txt
                                  ]
  --labels_file FILE              Specifies a file to read labels from in
                                  order to convert class IDs into labels. A
                                  label file is a simple text file where each
                                  line corresponds to a label. If unspecified,
                                  only a single output label is created for
                                  FIL
  --userid_column_name TEXT       Which column to use as the User ID.
                                  [default: userIdentityaccountId; required]
  --userid_filter TEXT            Specifying this value will filter all
                                  incoming data to only use rows with matching
                                  User IDs. Which column is used for the User
                                  ID is specified by `userid_column_name`
  --feature_scaler TEXT           Autoencoder feature scaler  [default:
                                  standard]
  --min_train_features INTEGER RANGE
                                  Number of features trained in the model
                                  [default: 1; x>=1]
  --viz_file FILE                 Save a visualization of the pipeline at the
                                  specified location
  --help                          Show this message and exit.

Commands:
  add-class        Add detected classifications to each message
  add-scores       Add probability scores to each message
  buffer           (Deprecated) Buffer results
  delay            (Deprecated) Delay results for a certain duration
  filter           Filter message by a classification threshold
  from-azure       Load messages from a Duo directory
  from-cloudtrail  Load messages from a Cloudtrail directory
  from-duo         Load messages from a Duo directory
  gen-viz          (Deprecated) Write out vizualization data frames
  inf-pytorch      Perform inference with PyTorch
  inf-triton       Perform inference with Triton
  monitor          Display throughput numbers at a specific point in the
                   pipeline
  preprocess       Convert messages to tokens
  serialize        Include & exclude columns from messages
  timeseries       Perform time series anomaly detection and add prediction.
  to-file          Write all messages to a file
  to-kafka         Write all messages to a Kafka cluster
  train-ae         Deserialize source data from JSON
  validate         Validates pipeline output against an expected output
```
The commands above correspond to the Morpheus stages that can be used to construct your DFP pipeline. Options are available to configure pipeline and stages.
The following table shows mapping between the main Morpheus CLI commands and underlying Morpheus Python stage classes:

| CLI Command    | Stage Class              | Python File              |
| ---------------| -------------------------| ---------------------------------------------------------
| from-azure     | AzureSourceStage         | morpheus/stages/input/azure_source_stage.py
| from-cloudtrail| CloudTrailSourceStage    | morpheus/stages/input/clout_trail_source_stage.py
| from-duo       | DuoSourceStage           | morpheus/stages/input/duo_source_stage.py
| train-ae       | TrainAEStage             | morpheus/stages/preprocess/train_ae_stage.py
| preprocess     | PreprocessAEStage        | morpheus/stages/preprocess/preprocess_ae_stage.py
| inf-pytorch    | AutoEncoderInferenceStage| morpheus/stages/inference/auto_encoder_inference_stage.py
| add-scores     | AddScoresStage           | morpheus/stages/postprocess/add_scores_stage.py
| serialize      | SerializeStage           | morpheus/stages/postprocess/serialize_stage.py
| to-file        | WriteToFileStage         | morpheus/stages/output/write_to_file_stage.py


## Morpheus DFP Stages

**Source stages** - These include `AzureSourceStage`, `CloudTrailSourceStage` and `DuoSourceStage`. They are responsible for reading log file(s) that match provided `--input_glob` (e.g. `/duo_logs/*.json`). Data is grouped by user so that each batch processed by the pipeline will only contain rows corresponding to a single user. Feature engineering also happens in this stage. All DFP source stages must extend `AutoencoderSourceStage` and implement the `files_to_dfs_per_user` abstract method. Feature columns can be managed by overriding the `derive_features` method. Otherwise, all columns from input data pass through to next stage.

**Preprocessing stages** - `TrainAEStage` can either train user models using data matching a provided `--train_data_glob` or load pre-trained models from file using `--pretrained_filename`. When using `--train_data_glob`, user models can be saved using the `--models_output_filename` option. The `--source_stage_class` must also be used with `--train_data_glob` so that the training stage knows how to read the training data. The autoencoder implementation from this [fork](https://github.com/efajardo-nv/dfencoder/tree/nv-updates) is used for user model training. The `PreprocessAEStage` is responsible for creating a Morpheus message that contains everything needed by the inference stage. For DFP inference, this stage must pass a `MultiInferenceAEMessage` to the inference stage. Each message will correspond to a single user and include the input feature columns, the user's model and trainingdata anomaly scores.

**Inference stage** - `AutoEncoderInferenceStage` calculates anomaly scores (i.e. reconstruction loss) and z-scores for each user input dataset.

**Postprocessing stage** - The DFP pipeline uses the `AddScoresStage` for postprocessing to add anomaly scores and zscores from previous inference stage with matching labels.

**Serialize stage** - `SerializeStage` is used to convert `MultiResponseProbsMessage` from previous stage to a `MessageMeta` to make it suitable for output (i.e. write to file or Kafka).

**Write stage** - `WriteToFileStage` writes input data with inference results to an output file path.


## CloudTrail DFP Pipeline

Run the following in your Morpheus container to start the CloudTrail DFP pipeline:

```
morpheus --log_level=DEBUG \
run --num_threads=1 --pipeline_batch_size=1024 --model_max_batch_size=1024 --use_cpp=False \
pipeline-ae \
--columns_file=morpheus/data/columns_ae_cloudtrail.txt \
--userid_filter=Account-123456789 \
--feature_scaler=gauss_rank \
from-cloudtrail \
--input_glob=models/datasets/validation-data/hammah-*.csv \
--max_files=200 \
train-ae \
--train_data_glob=models/datasets/training-data/hammah-*.csv \
--source_stage_class=morpheus.stages.input.cloud_trail_source_stage.CloudTrailSourceStage \
--seed=42 \
preprocess \
inf-pytorch \
add-scores \
timeseries --resolution=1m --zscore_threshold=8.0 --hot_start \
monitor --description='Inference rate' --unit inf \
validate \
--val_file_name=models/datasets/validation-data/hammah-user123-validation-data.csv \
--results_file_name=./.tmp/validation_results-ae.json \
--index_col=_index_ \
--exclude=event_dt \
--rel_tol=0.1 \
--overwrite \
serialize \
to-file --filename=./cloudtrail-detections.csv --overwrite
```

## Duo DFP Pipeline

First, trains user models from files in `models/datasets/training-data/duo` and saves user models to file. Pipeline then uses these models to run inference
on validation data in `models/datasets/validation-data/duo`. Inference results are written to `duo-detections.csv`.
```
morpheus --log_level=DEBUG \
run --num_threads=1 --pipeline_batch_size=1024 --model_max_batch_size=1024 --use_cpp=False \
pipeline-ae \
--columns_file=morpheus/data/columns_ae_duo.txt \
--userid_column_name=username \
--feature_scaler=standard \
from-duo \
--input_glob=models/datasets/validation-data/duo/*.json \
--max_files=200 \
monitor --description='Input rate' \
train-ae \
--train_data_glob=models/datasets/training-data/duo/*.json \
--source_stage_class=morpheus.stages.input.duo_source_stage.DuoSourceStage \
--seed=42 \
--train_epochs=1 \
--models_output_filename=models/hammah-models/duo_ae_user_models.pkl \
preprocess \
inf-pytorch \
monitor --description='Inference rate' --unit inf \
add-scores \
serialize \
to-file --filename=./duo-detections.csv --overwrite
```

Load pre-trained user models from file, `models/hammah-models/duo_ae_user_models.pkl`. Pipeline then uses these models to run inference
on validation data in `models/datasets/validation-data/duo`. Inference results are written to `duo-detections.csv`.
```
morpheus --log_level=DEBUG \
run --num_threads=1 --pipeline_batch_size=1024 --model_max_batch_size=1024 --use_cpp=False \
pipeline-ae \
--columns_file=morpheus/data/columns_ae_duo.txt \
--userid_column_name=username \
--feature_scaler=standard \
from-duo \
--input_glob=models/datasets/validation-data/duo/*.json \
--max_files=200 \
monitor --description='Input rate' \
train-ae \
--pretrained_filename=models/hammah-models/duo_ae_user_models.pkl \
preprocess \
inf-pytorch \
monitor --description='Inference rate' --unit inf \
add-scores \
serialize \
to-file --filename=./duo-detections.csv --overwrite
```

## Azure DFP Pipeline

First, trains user models from files in `models/datasets/training-data/azure` and saves user models to file. Pipeline then uses these models to run inference
on validation data in `models/datasets/validation-data/azure`. Inference results are written to `azure-detections.csv`.
```
morpheus --log_level=DEBUG \
run --num_threads=1 --pipeline_batch_size=1024 --model_max_batch_size=1024 --use_cpp=False \
pipeline-ae \
--columns_file=morpheus/data/columns_ae_azure.txt \
--userid_column_name=userPrincipalName \
--feature_scaler=standard \
from-azure \
--input_glob=models/datasets/validation-data/azure/*.json \
--max_files=200 \
train-ae \
--train_data_glob=models/datasets/training-data/azure/*.json \
--source_stage_class=morpheus.stages.input.azure_source_stage.AzureSourceStage \
--seed=42 \
--models_output_filename=models/hammah-models/azure_ae_user_models.pkl \
preprocess \
inf-pytorch \
monitor --description='Inference rate' --unit inf \
add-scores \
serialize \
to-file --filename=./azure-detections.csv --overwrite
```

Load pre-trained user models from file, `models/hammah-models/azure_ae_user_models.pkl`. Pipeline then uses these models to run inference
on validation data in `models/datasets/validation-data/azure`. Inference results are written to `azure-detections.csv`.
```
morpheus --log_level=DEBUG \
run --num_threads=1 --pipeline_batch_size=1024 --model_max_batch_size=1024 --use_cpp=False \
pipeline-ae \
--columns_file=morpheus/data/columns_ae_azure.txt \
--userid_column_name=userPrincipalName \
--feature_scaler=standard \
from-azure \
--input_glob=models/datasets/validation-data/azure/*.json \
--max_files=200 \
train-ae \
--pretrained_filename=models/hammah-models/azure_ae_user_models.pkl \
preprocess \
inf-pytorch \
monitor --description='Inference rate' --unit inf \
add-scores \
serialize \
to-file --filename=./azure-detections.csv --overwrite
```
