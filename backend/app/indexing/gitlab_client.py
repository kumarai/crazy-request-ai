from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("[gitlab]")


class GitLabClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def list_wiki_pages(self, project_id: int) -> list[dict]:
        pages: list[dict] = []
        page_num = 1

        async with httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=30.0
        ) as client:
            while True:
                resp = await client.get(
                    f"/api/v4/projects/{project_id}/wikis",
                    params={"per_page": 100, "page": page_num},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                pages.extend(batch)

                next_page = resp.headers.get("X-Next-Page", "")
                if not next_page:
                    break
                page_num = int(next_page)

        logger.info(
            "Listed %d wiki pages for project %d", len(pages), project_id
        )
        return pages

    async def get_wiki_page(self, project_id: int, slug: str) -> dict:
        async with httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=30.0
        ) as client:
            resp = await client.get(
                f"/api/v4/projects/{project_id}/wikis/{slug}"
            )
            resp.raise_for_status()
            return resp.json()

    async def get_project_info(self, project_id: int) -> dict:
        async with httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=30.0
        ) as client:
            resp = await client.get(f"/api/v4/projects/{project_id}")
            resp.raise_for_status()
            return resp.json()
