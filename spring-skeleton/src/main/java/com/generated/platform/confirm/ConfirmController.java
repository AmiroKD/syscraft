package com.generated.platform.confirm;

import cn.dev33.satoken.stp.StpUtil;
import com.generated.platform.admin.entity.EntitySnapshotEntity;
import com.generated.platform.admin.mapper.EntitySnapshotMapper;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.StringJoiner;

/**
 * 确认令牌预告端点。执行不在 endpoints 里：业务端点（生成的 delete）自己核销令牌，
 * 这里只负责「算影响行数、给令牌与预告文案」。
 *
 * <p>权限按实体动态判：@SaCheckPermission 需要编译期常量，动态实体名只能编程式校验。
 * 校验结果与注解走同一个 StpInterface，口径一致。</p>
 */
@RestController
@RequestMapping("/api/confirm")
public class ConfirmController {

    private final ConfirmExecutorRegistry registry;
    private final ConfirmService confirmService;
    private final EntitySnapshotMapper entitySnapshotMapper;

    public ConfirmController(ConfirmExecutorRegistry registry, ConfirmService confirmService,
                             EntitySnapshotMapper entitySnapshotMapper) {
        this.registry = registry;
        this.confirmService = confirmService;
        this.entitySnapshotMapper = entitySnapshotMapper;
    }

    public record PreviewRequest(
            @NotBlank(message = "不能为空") String entity,
            @NotEmpty(message = "不能为空") List<Long> ids) {
    }

    @PostMapping("/preview")
    public ApiResponse<ConfirmService.Ticket> preview(@Valid @RequestBody PreviewRequest request) {
        String entity = request.entity().trim();
        StpUtil.checkPermission("biz:" + entity + ":delete");

        EntitySnapshotEntity snapshot = entitySnapshotMapper.selectById(entity);
        if (snapshot == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "结构快照里没有这张表：" + entity);
        }

        List<Long> ids = canonical(request.ids());
        int impact = registry.require(entity).impact(ids);
        if (impact == 0) {
            throw new BizException(ErrorCode.CONFLICT, "没有可删除的记录（可能已被删除）");
        }

        String summary = "将删除「" + snapshot.getDisplayName() + "」 " + impact + " 条（id: "
                + join(ids) + "），令牌 5 分钟内有效";
        return ApiResponse.ok(confirmService.preview("DELETE", entity, join(ids), impact, summary));
    }

    /**
     * 更新预告：与删除预告同一套令牌三态机，op=UPDATE，影响行数 = ids 数。
     * valuesHash 由调用方对「将要应用的字段值」计算（规范 JSON + 排序键的 sha256），
     * 预告与执行两侧各带一份进指纹——用户确认的「改成什么」与真正写库的内容绑定，
     * 预告后被偷换值的执行会被指纹校验拦下（协议 4.7）。
     */
    public record PreviewUpdateRequest(
            @NotBlank(message = "不能为空") String entity,
            @NotEmpty(message = "不能为空") List<Long> ids,
            @NotBlank(message = "不能为空") String valuesHash) {
    }

    @PostMapping("/preview-update")
    public ApiResponse<ConfirmService.Ticket> previewUpdate(@Valid @RequestBody PreviewUpdateRequest request) {
        String entity = request.entity().trim();
        StpUtil.checkPermission("biz:" + entity + ":update");

        EntitySnapshotEntity snapshot = entitySnapshotMapper.selectById(entity);
        if (snapshot == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "结构快照里没有这张表：" + entity);
        }

        List<Long> ids = canonical(request.ids());
        int impact = ids.size();
        String summary = "将修改「" + snapshot.getDisplayName() + "」 " + impact + " 条（id: "
                + join(ids) + "），令牌 5 分钟内有效";
        return ApiResponse.ok(confirmService.preview("UPDATE", entity,
                join(ids) + "|v=" + request.valuesHash(), impact, summary));
    }

    /** 规范化：去重、排序。指纹与预告/执行两侧都用同一份，顺序不同不会导致指纹漂移 */
    private List<Long> canonical(List<Long> ids) {
        return new LinkedHashSet<>(ids).stream().sorted().toList();
    }

    private String join(List<Long> ids) {
        StringJoiner joiner = new StringJoiner(",");
        ids.forEach(id -> joiner.add(String.valueOf(id)));
        return joiner.toString();
    }
}
