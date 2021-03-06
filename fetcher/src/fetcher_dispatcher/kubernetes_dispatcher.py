#  Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License").
#  You may not use this file except in compliance with the License.
#  A copy of the License is located at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  or in the "license" file accompanying this file. This file is distributed
#  on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
#  express or implied. See the License for the specific language governing
#  permissions and limitations under the License.
import logging
from math import ceil
from typing import Dict, List, Optional

import kubernetes
from bai_k8s_utils.service_labels import ServiceLabels
from bai_kafka_utils.events import DownloadableContent, BenchmarkEvent, ContentSizeInfo
from bai_kafka_utils.utils import id_generator, md5sum

from fetcher_dispatcher.args import FetcherJobConfig
from fetcher_dispatcher.download_manager import DownloadDispatcher

BYTES_IN_MB = 1024 * 1024

logger = logging.getLogger(__name__)


def create_kubernetes_api_client(kubeconfig: str) -> kubernetes.client.ApiClient:
    logger.info("Initializing with KUBECONFIG=%s", kubeconfig)

    if kubeconfig:
        kubernetes.config.load_kube_config(kubeconfig)
    else:
        kubernetes.config.load_incluster_config()

    configuration = kubernetes.client.Configuration()
    return kubernetes.client.ApiClient(configuration)


def _align(n: int, align: int) -> int:
    r = n % align
    return n + (align - r) if r else n


class KubernetesDispatcher(DownloadDispatcher):
    DATA_SET_HASH_LABEL = "data-set-md5"

    ZK_NODE_PATH_ARG = "--zk-node-path"

    DST_ARG = "--dst"

    SRC_ARG = "--src"

    MD5_ARG = "--md5"

    FETCHER_CONTAINER_NAME = "fetcher"

    ZOOKEEPER_ENSEMBLE_HOSTS = "ZOOKEEPER_ENSEMBLE_HOSTS"

    TMP_DIR = "TMP_DIR"

    TMP_MOUNT_PATH = "/var/tmp/"

    TMP_VOLUME = "tmp-volume"

    VOLUME_SIZE_ALIGN = 4

    def __init__(self, service_name: str, kubeconfig: str, zk_ensemble: str, fetcher_job: FetcherJobConfig):
        self.service_name = service_name
        self.zk_ensemble = zk_ensemble
        self.fetcher_job = fetcher_job

        api_client = create_kubernetes_api_client(kubeconfig)

        self.batch_api_instance = kubernetes.client.BatchV1Api(api_client)
        self.core_api_instance = kubernetes.client.CoreV1Api(api_client)

    def _get_fetcher_job(
        self, task: DownloadableContent, event: BenchmarkEvent, zk_node_path: str, volume_claim_name: Optional[str]
    ) -> kubernetes.client.V1Job:

        job_metadata = kubernetes.client.V1ObjectMeta(
            namespace=self.fetcher_job.namespace, name=self._get_job_name(task), labels=self._get_labels(task, event)
        )

        job_status = kubernetes.client.V1JobStatus()

        pod_template = self._get_fetcher_pod_template(task, event, zk_node_path, volume_claim_name)
        job_spec = kubernetes.client.V1JobSpec(ttl_seconds_after_finished=self.fetcher_job.ttl, template=pod_template)

        return kubernetes.client.V1Job(
            api_version="batch/v1", kind="Job", spec=job_spec, status=job_status, metadata=job_metadata
        )

    # TODO Implement some human readable name from DataSet
    def _get_job_name(self, task: DownloadableContent) -> str:
        return "fetcher-" + id_generator()

    def _get_volume_name(self, task: DownloadableContent) -> str:
        return "fetcher-pv-" + id_generator()

    def _get_fetcher_pod_template(
        self, task: DownloadableContent, event: BenchmarkEvent, zk_node_path: str, volume_claim_name: Optional[str]
    ) -> kubernetes.client.V1PodTemplateSpec:
        pod_spec = self._get_fetcher_pod_spec(task, zk_node_path, volume_claim_name)
        return kubernetes.client.V1PodTemplateSpec(
            spec=pod_spec,
            metadata=kubernetes.client.V1ObjectMeta(
                labels=self._get_labels(task, event), annotations=self._get_fetcher_pod_annotations()
            ),
        )

    def _get_fetcher_pod_spec(self, task: DownloadableContent, zk_node_path: str, volume_claim_name: Optional[str]):
        volume = self._get_fetcher_volume(volume_claim_name)
        container = self._get_fetcher_container(task, zk_node_path)

        return kubernetes.client.V1PodSpec(
            containers=[container],
            volumes=[volume],
            restart_policy=self.fetcher_job.restart_policy,
            node_selector=self.fetcher_job.node_selector,
        )

    def _get_fetcher_volume(self, volume_claim_name: str = None):
        return kubernetes.client.V1Volume(
            name=KubernetesDispatcher.TMP_VOLUME,
            persistent_volume_claim=kubernetes.client.V1PersistentVolumeClaimVolumeSource(claim_name=volume_claim_name)
            if volume_claim_name
            else None,
            empty_dir=None if volume_claim_name else kubernetes.client.V1EmptyDirVolumeSource(),
        )

    def _get_fetcher_container(self, task: DownloadableContent, zk_node_path: str) -> kubernetes.client.V1Container:
        job_args = self._get_args(task, zk_node_path)
        env_list = self._get_env()
        return kubernetes.client.V1Container(
            name=KubernetesDispatcher.FETCHER_CONTAINER_NAME,
            image=self.fetcher_job.image,
            args=job_args,
            env=env_list,
            image_pull_policy=self.fetcher_job.pull_policy,
            volume_mounts=[
                kubernetes.client.V1VolumeMount(
                    name=KubernetesDispatcher.TMP_VOLUME, mount_path=KubernetesDispatcher.TMP_MOUNT_PATH
                )
            ],
        )

    def _get_fetcher_pod_annotations(self):
        return {"iam.amazonaws.com/role": "fetcher"}

    def _get_args(self, task: DownloadableContent, zk_node_path: str) -> List[str]:
        return [
            KubernetesDispatcher.SRC_ARG,
            task.src,
            KubernetesDispatcher.DST_ARG,
            task.dst,
            KubernetesDispatcher.ZK_NODE_PATH_ARG,
            zk_node_path,
            KubernetesDispatcher.MD5_ARG,
            task.md5,
        ]

    def _get_env(self) -> List[kubernetes.client.V1EnvVar]:
        return [
            kubernetes.client.V1EnvVar(name=KubernetesDispatcher.ZOOKEEPER_ENSEMBLE_HOSTS, value=self.zk_ensemble),
            kubernetes.client.V1EnvVar(name=KubernetesDispatcher.TMP_DIR, value=KubernetesDispatcher.TMP_MOUNT_PATH),
        ]

    def _get_labels(self, task: DownloadableContent, event: BenchmarkEvent) -> Dict[str, str]:
        labels = ServiceLabels.get_labels(self.service_name, event.client_id, event.action_id)
        labels[KubernetesDispatcher.DATA_SET_HASH_LABEL] = md5sum(task.src)
        return labels

    @staticmethod
    def get_label_selector(
        service_name: str, client_id: str, action_id: str = None, data_set: DownloadableContent = None
    ):
        selector = ServiceLabels.get_label_selector(service_name, client_id, action_id)
        if action_id and data_set:
            selector += f",{KubernetesDispatcher.DATA_SET_HASH_LABEL}={md5sum(data_set.src)}"
        return selector

    def _get_label_selector(self, client_id: str, action_id: str = None, data_set: DownloadableContent = None):
        return KubernetesDispatcher.get_label_selector(self.service_name, client_id, action_id, data_set)

    def dispatch_fetch(self, task: DownloadableContent, event: BenchmarkEvent, zk_node_path: str):
        try:
            volume_claim: str = None

            if not task.size_info:
                raise ValueError("Missing size_info data")

            volume_size = KubernetesDispatcher._get_volume_size(task.size_info)

            # Do we need a volume?
            if volume_size >= self.fetcher_job.volume.min_size:
                volume_claim = self._get_volume_name(task)
                pv_metadata = kubernetes.client.V1ObjectMeta(
                    namespace=self.fetcher_job.namespace, name=volume_claim, labels=self._get_labels(task, event)
                )

                volume_body = kubernetes.client.V1PersistentVolumeClaim(
                    metadata=pv_metadata,
                    spec=kubernetes.client.V1PersistentVolumeClaimSpec(
                        access_modes=["ReadWriteOnce"],
                        storage_class_name=self.fetcher_job.volume.storage_class,
                        resources=kubernetes.client.V1ResourceRequirements(requests={"storage": f"{volume_size}M"}),
                    ),
                )
                self.core_api_instance.create_namespaced_persistent_volume_claim(
                    self.fetcher_job.namespace, volume_body
                )

            fetcher_job = self._get_fetcher_job(task, event, zk_node_path, volume_claim)

            resp = self.batch_api_instance.create_namespaced_job(self.fetcher_job.namespace, fetcher_job, pretty=True)
            logger.debug("k8s response: %s", resp)
        except Exception:
            logger.exception("Failed to create a kubernetes job")
            raise

    def cancel_all(self, client_id: str, action_id: str = None) -> List[str]:
        logger.info(f"Removing jobs {client_id}/{action_id}")
        results = []
        action_id_label_selector = self._get_label_selector(client_id, action_id)

        jobs_response = self.batch_api_instance.delete_collection_namespaced_job(
            self.fetcher_job.namespace, label_selector=action_id_label_selector
        )
        logger.debug("k8s response: %s", jobs_response)
        logger.info(f"Removing pods {client_id}/{action_id}")
        results.append(str(jobs_response))
        pods_response = self.core_api_instance.delete_collection_namespaced_pod(
            self.fetcher_job.namespace, label_selector=action_id_label_selector
        )
        logger.debug("k8s response: %s", pods_response)
        logger.info(f"Removing volume claims {client_id}/{action_id}")
        results.append(str(pods_response))
        volumes_response = self.core_api_instance.delete_collection_namespaced_persistent_volume_claim(
            self.fetcher_job.namespace, label_selector=action_id_label_selector
        )
        logger.debug("k8s response: %s", volumes_response)
        results.append(str(volumes_response))

        return results

    def cleanup(self, task: DownloadableContent, event: BenchmarkEvent):
        delete_selector = self._get_label_selector(event.client_id, event.action_id, task)
        logger.info(delete_selector)

        job_response = self.batch_api_instance.delete_collection_namespaced_job(
            self.fetcher_job.namespace, label_selector=delete_selector
        )
        logger.info(job_response)

        pod_response = self.core_api_instance.delete_collection_namespaced_pod(
            self.fetcher_job.namespace, label_selector=delete_selector
        )
        logger.info(pod_response)

        volumes_response = self.core_api_instance.delete_collection_namespaced_persistent_volume_claim(
            self.fetcher_job.namespace, label_selector=delete_selector
        )
        logger.info(volumes_response)

    @staticmethod
    def _get_volume_size(size_info: ContentSizeInfo):
        # Max file + 20% just for any case.
        # Not less than 10% of total size
        # Round to the next X mb. X=4
        total_mb = ceil(size_info.total_size / BYTES_IN_MB)
        max_mb = ceil(size_info.max_size / BYTES_IN_MB)
        required_mb = int(ceil(max(max_mb * 1.2, total_mb * 0.1)))
        return _align(required_mb, KubernetesDispatcher.VOLUME_SIZE_ALIGN)
