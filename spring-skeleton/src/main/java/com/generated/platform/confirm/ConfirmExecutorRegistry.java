package com.generated.platform.confirm;

import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 实体 → 影响行数计算器。生成的 Service 在启动时把自己注册进来（模板保证不漏），
 * 预告端点据此算「这次会删多少条」，不需要为每个实体手写一份预告逻辑。
 *
 * <p>注册的 key 是实体名，与 adm_entity_snapshot.entity 一致；
 * 快照里没有的实体连预告都进不来。</p>
 */
@Component
public class ConfirmExecutorRegistry {

    public interface Executor {

        /** 预告时统计影响行数：只查不删 */
        int impact(List<Long> ids);
    }

    private final Map<String, Executor> executors = new ConcurrentHashMap<>();

    public void register(String entity, Executor executor) {
        executors.put(entity, executor);
    }

    public Executor require(String entity) {
        Executor executor = executors.get(entity);
        if (executor == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "结构快照里没有这张表，或该表不支持此操作：" + entity);
        }
        return executor;
    }
}
