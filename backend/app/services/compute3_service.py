"""Compute3 Turbo Mode Service.

Handles GPU job management through Compute3 API for faster LLM inference.
"""

import base64
import logging
import httpx
import asyncio
from typing import Optional, Dict, List, Any
from datetime import datetime
from functools import lru_cache

from app.config import get_settings
from app.services.llm_config import set_turbo_mode_state

logger = logging.getLogger(__name__)

# How long to wait for vLLM server to become ready (seconds)
VLLM_HEALTH_CHECK_TIMEOUT = 300  # 5 minutes
VLLM_HEALTH_CHECK_INTERVAL = 5  # Check every 5 seconds


class Compute3Job:
    """Represents a Compute3 GPU job."""
    
    def __init__(self, data: Dict[str, Any]):
        self.job_id: str = data.get("job_id", "")
        self.job_key: str = data.get("job_key", "")
        self.state: str = data.get("state", "unknown")
        self.gpu_type: str = data.get("gpu_type", "")
        self.gpu_count: int = data.get("gpu_count", 0)
        self.region: str = data.get("region", "")
        self.price_per_hour: float = data.get("price_per_hour", 0.0)
        self.price_per_second: float = data.get("price_per_second", 0.0)
        self.docker_image: str = data.get("docker_image", "")
        self.runtime: int = data.get("runtime", 0)
        self.hostname: Optional[str] = data.get("hostname")
        self.created_at: Optional[float] = data.get("created_at")
        self.started_at: Optional[float] = data.get("started_at")
        self.completed_at: Optional[float] = data.get("completed_at")
        self.completed: bool = data.get("completed", False)
        self.ports: Dict[str, Any] = data.get("ports", {})
        self.auth: bool = data.get("auth", False)
        self._raw = data
        # Track if the vLLM inference server is actually ready
        self._vllm_ready: bool = False
    
    @property
    def is_running(self) -> bool:
        """Check if the job is currently running."""
        return self.state in ("running", "queued", "pending")
    
    @property
    def is_ready(self) -> bool:
        """Check if the vLLM inference server is ready to accept requests."""
        return self.is_running and self._vllm_ready
    
    def set_ready(self, ready: bool):
        """Set the vLLM readiness status."""
        self._vllm_ready = ready
    
    @property
    def base_url(self) -> Optional[str]:
        """Get the OpenAI-compatible base URL for this job."""
        if self.hostname and self.is_running:
            # The hostname typically gives us access to the inference server
            return f"https://{self.hostname}/v1"
        return None
    
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "job_id": self.job_id,
            "state": self.state,
            "gpu_type": self.gpu_type,
            "gpu_count": self.gpu_count,
            "region": self.region,
            "price_per_hour": self.price_per_hour,
            "runtime": self.runtime,
            "hostname": self.hostname,
            "base_url": self.base_url,
            "is_running": self.is_running,
            "is_ready": self.is_ready,  # vLLM server ready status
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "completed": self.completed,
        }


class Compute3Service:
    """Service for managing Compute3 GPU jobs for Turbo Mode."""
    
    def __init__(self):
        self.settings = get_settings()
        self._active_job_id: Optional[str] = None
        self._active_job: Optional[Compute3Job] = None
    
    @property
    def is_available(self) -> bool:
        """Check if Compute3 is available (API key is set)."""
        return self.settings.turbo_mode_available
    
    @property
    def api_key(self) -> str:
        """Get the Compute3 API key."""
        return self.settings.compute3_api_key
    
    @property
    def api_base(self) -> str:
        """Get the Compute3 API base URL."""
        return self.settings.compute3_api_base
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for Compute3 API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
    
    async def get_balance(self) -> Dict[str, Any]:
        """Get current account balance."""
        if not self.is_available:
            return {"error": "Compute3 API key not configured"}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.api_base}/api/balance",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Error getting Compute3 balance: {e}")
                return {"error": str(e)}
    
    async def list_jobs(self, state: Optional[str] = None) -> List[Compute3Job]:
        """List all jobs, optionally filtered by state."""
        if not self.is_available:
            return []
        
        async with httpx.AsyncClient() as client:
            try:
                params = {}
                if state:
                    params["state"] = state
                
                response = await client.get(
                    f"{self.api_base}/api/jobs",
                    headers=self._get_headers(),
                    params=params,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                
                # Handle both list and dict responses
                if isinstance(data, list):
                    jobs = data
                else:
                    jobs = data.get("jobs", data.get("items", []))
                
                return [Compute3Job(job) for job in jobs]
            except httpx.HTTPError as e:
                logger.error(f"Error listing Compute3 jobs: {e}")
                return []
    
    async def get_job(self, job_id: str) -> Optional[Compute3Job]:
        """Get a specific job by ID."""
        if not self.is_available:
            return None
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.api_base}/api/jobs/{job_id}",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
                return Compute3Job(response.json())
            except httpx.HTTPError as e:
                logger.error(f"Error getting Compute3 job {job_id}: {e}")
                return None
    
    async def get_job_token(self, job_id: str) -> Optional[str]:
        """
        Get authentication token for a job with auth enabled.
        
        For jobs created with auth=true, this returns a Bearer token that
        must be included in requests to the job's HTTPS endpoint.
        
        See: https://docs.compute3.ai/api-reference/jobs/get-job-token
        
        Args:
            job_id: The job ID to get a token for
            
        Returns:
            Bearer token string or None on error
        """
        if not self.is_available:
            return None
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.api_base}/api/jobs/{job_id}/token",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                token = data.get("token")
                if token:
                    logger.debug(f"Got auth token for job {job_id}")
                return token
            except httpx.HTTPError as e:
                logger.error(f"Error getting token for job {job_id}: {e}")
                return None
    
    async def create_turbo_job(
        self,
        runtime: Optional[int] = None,
        gpu_type: Optional[str] = None,
        gpu_count: Optional[int] = None,
        model: Optional[str] = None,
        wait_for_ready: bool = False,
    ) -> Optional[Compute3Job]:
        """
        Create a new Turbo Mode GPU job running vLLM inference server.
        
        Args:
            runtime: Job runtime in seconds (default from config)
            gpu_type: GPU type (default: h100)
            gpu_count: Number of GPUs (default: 4)
            model: Model to run (default: MiniMaxAI/MiniMax-M2.1)
            wait_for_ready: If True, wait for vLLM server to become ready before returning
        
        Returns:
            Created job or None on error
            
        Note:
            Turbo mode is NOT enabled until the vLLM server is confirmed ready.
            Use get_active_turbo_job() to check readiness and enable turbo mode.
        """
        if not self.is_available:
            logger.error("Compute3 API key not configured")
            return None
        
        # Use provided values or defaults from config
        runtime = runtime or self.settings.compute3_default_runtime
        gpu_type = gpu_type or self.settings.compute3_gpu_type
        gpu_count = gpu_count or self.settings.compute3_gpu_count
        model = model or self.settings.compute3_model
        docker_image = self.settings.compute3_docker_image
        
        # Build the vLLM command for serving the model
        # Use 'vllm serve' which is the recommended way to start vLLM
        # --trust-remote-code is required for models with custom code (like MiniMax-M2.1)
        # --max-model-len limits context length to fit in available KV cache memory
        # --gpu-memory-utilization maximizes GPU memory usage for KV cache
        command = (
            f"vllm serve {model} --host 0.0.0.0 --port 8000 "
            f"--tensor-parallel-size {gpu_count} --trust-remote-code "
            f"--max-model-len 65536 --gpu-memory-utilization 0.95"
        )
        command_b64 = base64.b64encode(command.encode()).decode()
        
        job_data = {
            "gpu_type": gpu_type.upper(),
            "gpu_count": gpu_count,
            "docker_image": docker_image,
            "command": command_b64,
            "runtime": runtime,
            "interruptible": False,  # We want guaranteed availability for turbo mode
            "ports": {"lb": 8000},  # Expose port 8000 through load balancer
            "auth": True,  # Enable auth on load balancer - token fetched via /api/jobs/{id}/token
        }
        
        logger.info(f"Starting Turbo Mode job with image={docker_image}, model={model}, gpus={gpu_count}x{gpu_type}")
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.api_base}/api/jobs",
                    headers=self._get_headers(),
                    json=job_data,
                    timeout=60.0,
                )
                response.raise_for_status()
                job = Compute3Job(response.json())
                
                # Store as active job
                self._active_job_id = job.job_id
                self._active_job = job
                
                logger.info(f"Created Turbo Mode job {job.job_id} with {gpu_count}x {gpu_type}")
                logger.info(f"vLLM server starting at {job.base_url} - waiting for readiness...")
                
                # DO NOT enable turbo mode yet - the vLLM server needs time to start
                # Turbo mode will be enabled when get_active_turbo_job() confirms readiness
                
                # Optionally wait for the server to become ready
                if wait_for_ready and job.base_url:
                    is_ready = await self.wait_for_vllm_ready(job)
                    if is_ready:
                        # Use JWT token if auth is enabled (fetched via the
                        # Compute3 API, mirroring get_active_turbo_job()).
                        auth_token = None
                        if job.auth:
                            auth_token = await self.get_job_token(job.job_id)
                        auth_token = auth_token or self.api_key
                        set_turbo_mode_state(True, job.base_url, auth_token)
                        logger.info(f"Turbo Mode enabled - vLLM server ready at {job.base_url}")
                    else:
                        logger.warning(f"vLLM server did not become ready in time")
                
                return job
            except httpx.HTTPError as e:
                logger.error(f"Error creating Compute3 job: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Response: {e.response.text}")
                return None
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        if not self.is_available:
            return False
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.delete(
                    f"{self.api_base}/api/jobs/{job_id}",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
                
                # Clear active job if this was it
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    self._active_job = None
                    # Reset turbo mode state
                    set_turbo_mode_state(False)
                
                logger.info(f"Cancelled Turbo Mode job {job_id}")
                return True
            except httpx.HTTPError as e:
                logger.error(f"Error cancelling Compute3 job {job_id}: {e}")
                return False
    
    async def extend_job(self, job_id: str, additional_seconds: int) -> Optional[Compute3Job]:
        """Extend a running job's runtime."""
        if not self.is_available:
            return None
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{self.api_base}/api/jobs/{job_id}",
                    headers=self._get_headers(),
                    json={"additional_runtime": additional_seconds},
                    timeout=30.0,
                )
                response.raise_for_status()
                return Compute3Job(response.json())
            except httpx.HTTPError as e:
                logger.error(f"Error extending Compute3 job {job_id}: {e}")
                return None
    
    async def get_job_logs(self, job_id: str) -> str:
        """Get logs from a job."""
        if not self.is_available:
            return ""
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.api_base}/api/jobs/{job_id}/logs",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return data.get("logs", str(data))
            except httpx.HTTPError as e:
                logger.error(f"Error getting Compute3 job logs: {e}")
                return ""
    
    async def check_vllm_health(self, base_url: str, auth_token: Optional[str] = None) -> bool:
        """
        Check if the vLLM server is ready to accept requests.
        
        vLLM exposes several health check endpoints:
        - /health (returns 200 when ready)
        - /v1/models (lists available models when ready)
        
        Args:
            base_url: The base URL of the vLLM server (e.g., https://hostname/v1)
            auth_token: JWT token for authentication (if job has auth=True)
            
        Returns:
            True if the server is ready, False otherwise
        """
        # Remove /v1 suffix if present to get the root URL
        root_url = base_url.rstrip("/")
        if root_url.endswith("/v1"):
            root_url = root_url[:-3]
        
        # Use provided auth token, or fall back to API key
        bearer_token = auth_token or self.api_key
        auth_headers = {"Authorization": f"Bearer {bearer_token}"}
        
        async with httpx.AsyncClient() as client:
            # Try the /health endpoint first (most reliable)
            try:
                response = await client.get(
                    f"{root_url}/health",
                    headers=auth_headers,
                    timeout=10.0,
                )
                if response.status_code == 200:
                    logger.info(f"vLLM server health check passed: {root_url}/health")
                    return True
                elif response.status_code == 403:
                    logger.debug(f"vLLM health check got 403 - auth issue or server not ready")
            except httpx.HTTPError:
                pass
            
            # Try /v1/models endpoint (standard OpenAI-compatible endpoint)
            try:
                response = await client.get(
                    f"{root_url}/v1/models",
                    headers=auth_headers,
                    timeout=10.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    models = data.get("data", [])
                    if models:
                        logger.info(f"vLLM server ready with models: {[m.get('id') for m in models]}")
                        return True
                elif response.status_code == 403:
                    logger.debug(f"vLLM /v1/models got 403 - auth issue or server not ready")
            except httpx.HTTPError:
                pass
            
            # Try a simple ping to the /v1/chat/completions endpoint with minimal request
            # This can fail with 400 (bad request) but a non-404 response means the server is up
            try:
                response = await client.post(
                    f"{root_url}/v1/chat/completions",
                    headers={
                        **auth_headers,
                        "Content-Type": "application/json"
                    },
                    json={"model": "test", "messages": []},  # Invalid but should get a 400/422, not 404
                    timeout=10.0,
                )
                # 200-499 range means server is responding (excluding 404 which means not found)
                # 403 specifically means auth issue, which could be either wrong token OR server not ready
                # We should NOT consider 403 as "ready" since we can't actually use it
                if response.status_code == 200 or (response.status_code >= 400 and response.status_code < 500 and response.status_code not in (403, 404)):
                    logger.info(f"vLLM server responding (status {response.status_code})")
                    return True
                elif response.status_code == 403:
                    logger.debug(f"vLLM chat/completions got 403 - auth issue")
            except httpx.HTTPError:
                pass
            
            return False
    
    async def wait_for_vllm_ready(
        self, 
        job: Compute3Job, 
        timeout: int = VLLM_HEALTH_CHECK_TIMEOUT,
        interval: int = VLLM_HEALTH_CHECK_INTERVAL
    ) -> bool:
        """
        Wait for the vLLM server to become ready.
        
        Args:
            job: The Compute3 job to check
            timeout: Maximum time to wait in seconds
            interval: Time between health checks in seconds
            
        Returns:
            True if the server became ready, False if timeout
        """
        if not job.base_url:
            logger.warning(f"Job {job.job_id} has no base URL yet")
            return False
        
        logger.info(f"Waiting for vLLM server to become ready at {job.base_url}...")
        
        elapsed = 0
        while elapsed < timeout:
            # First, refresh job status to ensure it's still running
            refreshed_job = await self.get_job(job.job_id)
            if not refreshed_job or not refreshed_job.is_running:
                logger.warning(f"Job {job.job_id} is no longer running (state: {refreshed_job.state if refreshed_job else 'unknown'})")
                return False
            
            # Update base_url from refreshed job (hostname might have changed)
            if refreshed_job.base_url:
                # Use JWT token for authentication if job has auth enabled
                auth_token = None
                if refreshed_job.auth:
                    auth_token = await self.get_job_token(refreshed_job.job_id)
                if await self.check_vllm_health(refreshed_job.base_url, auth_token):
                    job.set_ready(True)
                    # Copy job_key to original job object for token generation
                    job.job_key = refreshed_job.job_key
                    logger.info(f"vLLM server is ready after {elapsed}s")
                    return True
            
            await asyncio.sleep(interval)
            elapsed += interval
            
            if elapsed % 30 == 0:  # Log progress every 30 seconds
                logger.info(f"Still waiting for vLLM server... ({elapsed}s / {timeout}s)")
        
        logger.error(f"vLLM server did not become ready within {timeout}s")
        return False
    
    async def get_active_turbo_job(self) -> Optional[Compute3Job]:
        """
        Get the currently active turbo mode job.
        
        Only returns a job and enables turbo mode if:
        1. The job is running
        2. The vLLM server is ready to accept requests
        """
        # First check cached job
        if self._active_job_id:
            job = await self.get_job(self._active_job_id)
            if job and job.is_running:
                self._active_job = job
                logger.debug(f"Cached job {job.job_id}: auth={job.auth}")
                
                # Check if vLLM server is ready before enabling turbo mode
                if job.base_url:
                    # Fetch auth token from Compute3 API if job has auth enabled
                    auth_token = None
                    if job.auth:
                        auth_token = await self.get_job_token(job.job_id)
                        if auth_token:
                            logger.info(f"Using auth token for health check (token_len={len(auth_token)})")
                        else:
                            logger.warning(f"Failed to get auth token for job {job.job_id}")
                    
                    is_ready = await self.check_vllm_health(job.base_url, auth_token)
                    job.set_ready(is_ready)
                    
                    if is_ready:
                        # Pass the auth token for LLM calls
                        api_token = auth_token or self.api_key
                        set_turbo_mode_state(True, job.base_url, api_token)
                    else:
                        # Server not ready yet, don't enable turbo mode for LLM calls
                        logger.debug(f"vLLM server not ready yet at {job.base_url}")
                        set_turbo_mode_state(False)
                
                return job
            else:
                # Job finished, clear cache
                self._active_job_id = None
                self._active_job = None
                set_turbo_mode_state(False)
        
        # Look for any running turbo mode jobs
        jobs = await self.list_jobs(state="running")
        logger.debug(f"Found {len(jobs)} running jobs")
        for job in jobs:
            logger.debug(f"Job {job.job_id}: image={job.docker_image}, state={job.state}")
            # Check if this is a turbo mode job (has vLLM in image)
            if "vllm" in job.docker_image.lower():
                # Fetch full job details
                full_job = await self.get_job(job.job_id)
                if full_job:
                    logger.info(f"Job {job.job_id}: auth={full_job.auth}")
                    job = full_job
                else:
                    logger.warning(f"Failed to fetch full job details for {job.job_id}")
                
                self._active_job_id = job.job_id
                self._active_job = job
                
                # Check if vLLM server is ready before enabling turbo mode
                if job.base_url:
                    # Fetch auth token from Compute3 API if job has auth enabled
                    auth_token = None
                    if job.auth:
                        auth_token = await self.get_job_token(job.job_id)
                        if auth_token:
                            logger.info(f"Using auth token for health check (token_len={len(auth_token)})")
                        else:
                            logger.warning(f"Failed to get auth token for job {job.job_id}")
                    
                    is_ready = await self.check_vllm_health(job.base_url, auth_token)
                    job.set_ready(is_ready)
                    
                    if is_ready:
                        # Pass the auth token for LLM calls
                        api_token = auth_token or self.api_key
                        set_turbo_mode_state(True, job.base_url, api_token)
                    else:
                        # Server not ready yet, don't enable turbo mode for LLM calls
                        logger.debug(f"vLLM server not ready yet at {job.base_url}")
                        set_turbo_mode_state(False)
                
                return job
        
        # No active job found, ensure turbo mode is disabled
        set_turbo_mode_state(False)
        return None
    
    async def get_turbo_base_url(self) -> Optional[str]:
        """Get the base URL for the active turbo mode inference server."""
        job = await self.get_active_turbo_job()
        if job and job.base_url:
            return job.base_url
        return None
    
    async def is_turbo_mode_enabled(self) -> bool:
        """Check if turbo mode is currently enabled (has an active job)."""
        job = await self.get_active_turbo_job()
        return job is not None and job.is_running
    
    def get_turbo_status(self) -> Dict[str, Any]:
        """Get synchronous turbo mode status (cached data only)."""
        is_running = self._active_job is not None and self._active_job.is_running
        is_ready = self._active_job is not None and self._active_job.is_ready
        
        return {
            "available": self.is_available,
            "active_job_id": self._active_job_id,
            "active": is_running,  # Job is running
            "ready": is_ready,     # vLLM server is ready for requests
            "job": self._active_job.to_dict() if self._active_job else None,
        }
    
    async def get_turbo_status_async(self) -> Dict[str, Any]:
        """Get turbo mode status with live health check."""
        job = await self.get_active_turbo_job()
        
        is_running = job is not None and job.is_running
        is_ready = job is not None and job.is_ready
        
        return {
            "available": self.is_available,
            "active_job_id": job.job_id if job else None,
            "active": is_running,  # Job is running
            "ready": is_ready,     # vLLM server is ready for requests
            "job": job.to_dict() if job else None,
        }


# Singleton instance
_compute3_service: Optional[Compute3Service] = None


def get_compute3_service() -> Compute3Service:
    """Get the Compute3 service singleton."""
    global _compute3_service
    if _compute3_service is None:
        _compute3_service = Compute3Service()
    return _compute3_service
