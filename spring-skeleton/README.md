# spring-skeleton

Java 固定骨架（`platform/` 层），31 个类，手写一次，随每个版本目录整目录复制。

- 包根：`com.generated.platform`
- 依赖只有七个：web / validation / actuator / `mybatis-plus-spring-boot3-starter` /
  `sa-token-spring-boot3-starter` + `sa-token-jwt` / `caffeine` / `mysql-connector-j`
- 业务代码不在这里：`generated/` 由模板渲染，见《代码生成器设计》

类清单见 `../../output/平台骨架接口清单-v1.0.md`。

本目录不参与生成；新增端点需改骨架并升级版本（`代码生成器设计` 10 节已列为局限）。
