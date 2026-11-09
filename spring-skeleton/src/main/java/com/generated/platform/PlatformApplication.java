package com.generated.platform;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 骨架启动类。
 *
 * <p>依赖方向固定为 generated → platform：生成的业务代码可以调 platform 的类，
 * platform 不反向依赖 generated（禁令 D6）。</p>
 *
 * <p>扫描根必须是 {@code com.generated} 而不是 {@code com.generated.platform}：
 * 生成物在 {@code com.generated.generated}，是兄弟包，用默认扫描根会整批漏掉——
 * 现象是接口全部 404，而启动毫无报错。</p>
 */
@SpringBootApplication(scanBasePackages = "com.generated")
// Mapper 扫描不在这里：adm 与 biz 各有一套 SqlSessionFactory，
// 扫描入口分别在 AdmDataSourceConfig 与 BizDataSourceConfig（两库不能共用一个工厂）
public class PlatformApplication {

    public static void main(String[] args) {
        SpringApplication.run(PlatformApplication.class, args);
    }
}
