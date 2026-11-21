# SkyWalking GraphQL 接口速查（L1-L6）

> 对应专栏附录 A「SkyWalking GraphQL 全接口手册（L1-L7）」的精简版。所有请求需带 `duration`（否则 OAP 返回全历史数据）。

## 通用信息

- 端点：`{OAP}/graphql`，`Content-Type: application/json`
- duration 格式：`{"step":"MINUTE","start":"2026-06-30 1325","end":"2026-06-30 1341"}`（东八区）
- 认证：默认不开启；开启时请求头 `Authentication: <token>`
- 服务/端点 ID 是 Base64 编码（例如 `order-svc.1`、`{serviceId}_{HTTP方法:path 的 Base64}`）

## L1 服务列表

```
query queryServices($layer: String!) {
  services: listServices(layer: $layer) { id value: name group normal }
}
```

## L1 全局 TopN（端点驱动入口）

```
query queryData($duration: Duration!, $expression50: String!) {
  expression50: execExpression(expression: $expression50, entity: {}, duration: $duration) {
    results { values { name: id value owner { serviceID serviceName endpointID endpointName } } }
  }
}
// variables.expression50 = top_n(endpoint_resp_time,10,des,attr0='GENERAL')
```

返回 `values[].name` 形如 `"order-svc - GET:/order/test/sql/real-slow"`，`values[].value` 为 RT(ms)。

## L6 Trace 列表（按耗时降序）

```
query queryTraces($condition: TraceQueryCondition) {
  data: queryBasicTraces(condition: $condition) {
    traces { key: segmentId duration start isError traceIds }
  }
}
// condition：serviceId, endpointId(可选), queryOrder=BY_DURATION, paging
```

## L6 Trace 详情（完整 Span 树）

```
query queryTrace($traceId: ID!) {
  trace: queryTrace(traceId: $traceId) {
    spans { serviceCode endpointName startTime endTime isError type component peer tags { key value } }
  }
}
```

## 常用表达式速查

| 表达式 | 含义 | 单位/换算 |
|--------|------|----------|
| `top_n(endpoint_resp_time,10,des,attr0='GENERAL')` | 最慢端点 Top10 | ms |
| `top_n(service_cpm,10,des,attr0='GENERAL')` | 调用量 Top10 | calls/min |
| `avg(service_apdex)/10000` | 服务 Apdex | 0-1 |
| `avg(service_sla)/100` | 服务成功率 | % |
| `service_resp_time` | 服务平均 RT | ms |
| `instance_jvm_*` | JVM 指标（CPU/GC/内存/线程） | 多个 |
| `service_relation_client_resp_time` | 客户端视角调用延迟 | ms |
| `service_relation_server_resp_time` | 服务端视角处理时间 | ms |

## 网络延迟（进阶，第 7 章）

`网络延迟 = service_relation_client_resp_time - service_relation_server_resp_time`
差值 > 100ms 视为显著网络延迟。