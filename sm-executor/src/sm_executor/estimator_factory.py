import logging

from addict import addict
from bai_kafka_utils.executors.descriptor import Descriptor, DistributedStrategy
from sagemaker import Session
from sagemaker.estimator import EstimatorBase, Framework
from sagemaker.mxnet import MXNet
from sagemaker.tensorflow import TensorFlow
from typing import Callable

from sm_executor.args import SageMakerExecutorConfig
from sm_executor.frameworks import MXNET_FRAMEWORK, TENSORFLOW_FRAMEWORK

MPI_OPTIONS = "-x HOROVOD_HIERARCHICAL_ALLREDUCE=1 -x HOROVOD_FUSION_THRESHOLD=16777216 -x TF_CPP_MIN_LOG_LEVEL=0"

EstimatorFactory = Callable[[Session, Descriptor, str, SageMakerExecutorConfig], EstimatorBase]

logger = logging.getLogger(__name__)


def create_tensorflow_estimator(
    session: Session, descriptor: Descriptor, source_dir: str, config: SageMakerExecutorConfig
) -> Framework:
    kwargs = _create_common_estimator_args(session, descriptor, source_dir, config)

    if descriptor.strategy == DistributedStrategy.HOROVOD:
        kwargs.distributions.mpi = addict.Dict(
            enabled=True, processes_per_host=descriptor.processes_per_instance, custom_mpi_options=MPI_OPTIONS
        )

    kwargs.script_mode = True
    return TensorFlow(**kwargs)


def create_mxnet_estimator(
    session: Session, descriptor: Descriptor, source_dir: str, config: SageMakerExecutorConfig
) -> Framework:
    kwargs = _create_common_estimator_args(session, descriptor, source_dir, config)
    return MXNet(**kwargs)


def _create_common_estimator_args(
    session: Session, descriptor: Descriptor, source_dir: str, config: SageMakerExecutorConfig
) -> addict.Dict:
    return addict.Dict(
        source_dir=source_dir,
        entry_point="tmp_entry.py",
        sagemaker_session=session,
        image_name=descriptor.docker_image,
        py_version="py3",
        framework_version=descriptor.framework_version,
        train_instance_type=descriptor.instance_type,
        train_instance_count=descriptor.num_instances,
        role=config.sm_role,
        output_path=f"s3://{config.s3_output_bucket}",
        security_group_ids=config.security_group_ids,
        subnets=config.subnets,
    )


def create_estimator(
    session: Session, descriptor: Descriptor, source_dir: str, config: SageMakerExecutorConfig
) -> EstimatorBase:
    factories = {MXNET_FRAMEWORK: create_mxnet_estimator, TENSORFLOW_FRAMEWORK: create_tensorflow_estimator}
    try:
        factory: EstimatorFactory = factories[descriptor.framework]
    except KeyError:
        logger.exception(
            f"Descriptor framework seems to be unknown. This should never happen. Supported: {factories.keys()}"
        )
        raise
    return factory(session, descriptor, source_dir, config)