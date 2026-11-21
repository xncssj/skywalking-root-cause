"""SkyWalking OAP GraphQL 零依赖客户端。

设计来源：专栏《SkyWalking 性能分析引擎》第 3 章 + 附录 A（全接口手册）。
仅用 Python 标准库（urllib），无需 pip install 任何依赖，便于在任意环境跑通。

用法示例:
    from sw_client import SwClient

    client = SwClient(base_url="http://oap-host:12800/graphql")
    services = client.list_services()
    slow = client.top_slow_endpoints(limit=10)
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


class SwClientError(RuntimeError):
    """SkyWalking OAP 查询失败。"""


def _duration_string(dt: datetime) -> str:
    """SkyWalking duration 字符串格式 yyyy-MM-dd HHmm（东八区，step=MINUTE）。"""
    return dt.strftime("%Y-%m-%d %H%M")


class SwClient:
    """SkyWalking GraphQL 客户端（覆盖 L1/L2/Trace 所需的最小接口集）。"""

    def __init__(self, base_url: str, timeout: int = 15, token: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def gql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables}
        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.token:
            req.add_header("Authentication", self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 网络/解析错误统一上抛
            raise SwClientError(f"GraphQL 请求失败: {exc}") from exc
        if "errors" in body:
            raise SwClientError(f"GraphQL 返回错误: {body['errors']}")
        return body.get("data", {})

    # ------------------------------------------------------------------
    # L1 层：服务列表 + 全局 TopN 指标（1 次请求合并 6 个表达式）
    # ------------------------------------------------------------------
    def list_services(self, layer: str = "GENERAL") -> List[Dict[str, Any]]:
        query = """
        query queryServices($layer: String!) {
          services: listServices(layer: $layer) {
            id value: name group normal
          }
        }
        """
        data = self.gql(query, {"layer": layer})
        return data.get("services", [])

    def top_slow_endpoints(
        self, start: datetime, end: datetime, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """execExpression：top_n(endpoint_resp_time) 一次请求定位最慢端点（端点驱动入口）。"""
        query = """
        query queryData($duration: Duration!, $expression50: String!) {
          expression50: execExpression(expression: $expression50, entity: {}, duration: $duration) {
            type
            results {
              values {
                name: id
                value
                owner { serviceID serviceName endpointID endpointName }
              }
            }
          }
        }
        """
        variables = {
            "duration": {
                "start": _duration_string(start),
                "end": _duration_string(end),
                "step": "MINUTE",
            },
            "expression50": f"top_n(endpoint_resp_time,{limit},des,attr0='GENERAL')",
        }
        data = self.gql(query, variables)
        values = (data.get("expression50", {}).get("results") or [{}])[0].get("values", [])
        out: List[Dict[str, Any]] = []
        for v in values:
            owner = v.get("owner") or {}
            try:
                rt = float(v.get("value"))
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "service_name": owner.get("serviceName"),
                    "service_id": owner.get("serviceID"),
                    "endpoint_name": owner.get("endpointName"),
                    "endpoint_id": owner.get("endpointID"),
                    "resp_time_ms": rt,
                }
            )
        return out

    # ------------------------------------------------------------------
    # L6 层：Trace 查询
    # ------------------------------------------------------------------
    def query_basic_traces(
        self,
        start: datetime,
        end: datetime,
        service_id: str,
        endpoint_id: Optional[str] = None,
        order: str = "BY_DURATION",
        page_size: int = 3,
    ) -> List[Dict[str, Any]]:
        query = """
        query queryTraces($condition: TraceQueryCondition) {
          data: queryBasicTraces(condition: $condition) {
            traces {
              key: segmentId
              duration
              start
              isError
              traceIds
            }
          }
        }
        """
        condition = {
            "queryDuration": {
                "start": _duration_string(start),
                "end": _duration_string(end),
                "step": "MINUTE",
            },
            "paging": {"pageNum": 1, "pageSize": page_size},
            "traceState": "ALL",
            "queryOrder": order,
            "serviceId": service_id,
        }
        if endpoint_id:
            condition["endpointId"] = endpoint_id
        data = self.gql(query, {"condition": condition})
        return data.get("data", {}).get("traces", [])

    def query_trace(self, trace_id: str) -> Dict[str, Any]:
        query = """
        query queryTrace($traceId: ID!) {
          trace: queryTrace(traceId: $traceId) {
            spans {
              traceId segmentId serviceCode endpointName startTime endTime isError
              type component peer layer
              tags { key value }
            }
          }
        }
        """
        data = self.gql(query, {"traceId": trace_id})
        return data.get("trace", {})


# ----------------------------------------------------------------------
# CLI 自检（无需 OAP，打印占位说明）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("SwClient 已就绪。示例:")
    print("  client = SwClient('http://oap-host:12800/graphql')")
    print("  client.top_slow_endpoints(start, end, limit=10)")
    sys.exit(0)
