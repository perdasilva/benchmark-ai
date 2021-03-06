import json
import os
from unittest import mock

import pytest
from bai_kafka_utils.executors.descriptor import DescriptorConfig

from transpiler.config import BaiConfig, EnvironmentInfo

from bai_kafka_utils.kafka_service import KafkaServiceConfig, DEFAULT_NUM_PARTITIONS, DEFAULT_REPLICATION_FACTOR
from executor.config import ExecutorConfig


BOOTSTRAP_SERVERS = ["K1", "K2"]
KUBECTL = "path/cfg"
LOGGING_LEVEL = "WARN"
CONSUMER_TOPIC = "IN"
PRODUCER_TOPIC = "OUT"
CMD_SUBMIT_TOPIC = "CMD_SUBMIT"
CMD_RETURN_TOPIC = "CMD_RETURN"
STATUS_TOPIC = "STATUS_TOPIC"
BOOTSTRAP_SERVERS_ARG = ",".join(BOOTSTRAP_SERVERS)
VALID_STRATEGIES = "s1,s2"
VALID_FRAMEWORKS = ",f1,f2"
VALID_EXECUTION_ENGINES = "e1,e2"
PULLER_MOUNT_CHMOD = "700"
PULLER_DOCKER_IMAGE = "example/docker:img"
METRICS_PUSHER_DOCKER_IMAGE = "metrics_pusher/docker:img"
METRICS_EXTRACTOR_DOCKER_IMAGE = "metrics_extractor/docker:img"
CRON_JOB_DOCKER_IMAGE = "cron_job/docker:img"
JOB_STATUS_TRIGGER_DOCKER_IMAGE = "job_status_trigger/docker:image"
SUPPRESS_JOB_AFFINITY = True


@pytest.fixture
def mock_env(mocker, mock_availability_zones):
    mocker.patch.object(os, "environ", {"AVAILABILITY_ZONES": json.dumps(mock_availability_zones)})


@mock.patch("executor.executor.create_executor")
def test_main(mock_create_executor, mock_availability_zones, mock_env):
    from executor.__main__ import main

    main(
        f" --consumer-topic {CONSUMER_TOPIC} "
        f" --producer-topic {PRODUCER_TOPIC} "
        f" --cmd-submit-topic {CMD_SUBMIT_TOPIC}"
        f" --cmd-return-topic {CMD_RETURN_TOPIC}"
        f" --status-topic {STATUS_TOPIC} "
        f" --bootstrap-servers {BOOTSTRAP_SERVERS_ARG} "
        f" --logging-level {LOGGING_LEVEL} "
        f" --kubectl {KUBECTL} "
        f" --transpiler-valid-strategies {VALID_STRATEGIES} "
        f" --transpiler-valid-frameworks {VALID_FRAMEWORKS} "
        f" --valid-execution-engines {VALID_EXECUTION_ENGINES} "
        f" --transpiler-puller-mount-chmod {PULLER_MOUNT_CHMOD} "
        f" --transpiler-puller-docker-image {PULLER_DOCKER_IMAGE} "
        f" --transpiler-metrics-pusher-docker-image {METRICS_PUSHER_DOCKER_IMAGE} "
        f" --transpiler-metrics-extractor-docker-image {METRICS_EXTRACTOR_DOCKER_IMAGE} "
        f" --transpiler-cron-job-docker-image {CRON_JOB_DOCKER_IMAGE} "
        f" --transpiler-job-status-trigger-docker-image {JOB_STATUS_TRIGGER_DOCKER_IMAGE} "
        f" --suppress-job-affinity"
    )

    expected_common_kafka_cfg = KafkaServiceConfig(
        consumer_topic=CONSUMER_TOPIC,
        producer_topic=PRODUCER_TOPIC,
        cmd_submit_topic=CMD_SUBMIT_TOPIC,
        cmd_return_topic=CMD_RETURN_TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        logging_level=LOGGING_LEVEL,
        status_topic=STATUS_TOPIC,
        replication_factor=min(DEFAULT_REPLICATION_FACTOR, len(BOOTSTRAP_SERVERS)),
        num_partitions=DEFAULT_NUM_PARTITIONS,
    )

    expected_executor_config = ExecutorConfig(
        kubectl=KUBECTL,
        valid_execution_engines=VALID_EXECUTION_ENGINES.split(","),
        descriptor_config=DescriptorConfig(
            valid_strategies=VALID_STRATEGIES.split(","), valid_frameworks=VALID_FRAMEWORKS.split(",")
        ),
        bai_config=BaiConfig(
            puller_mount_chmod=PULLER_MOUNT_CHMOD,
            puller_docker_image=PULLER_DOCKER_IMAGE,
            metrics_pusher_docker_image=METRICS_PUSHER_DOCKER_IMAGE,
            metrics_extractor_docker_image=METRICS_EXTRACTOR_DOCKER_IMAGE,
            cron_job_docker_image=CRON_JOB_DOCKER_IMAGE,
            job_status_trigger_docker_image=JOB_STATUS_TRIGGER_DOCKER_IMAGE,
            suppress_job_affinity=SUPPRESS_JOB_AFFINITY,
        ),
        environment_info=EnvironmentInfo(availability_zones=mock_availability_zones),
    )

    mock_create_executor.assert_called_with(expected_common_kafka_cfg, expected_executor_config)
