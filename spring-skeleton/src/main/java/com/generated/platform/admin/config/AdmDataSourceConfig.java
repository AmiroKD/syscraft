package com.generated.platform.admin.config;

import com.zaxxer.hikari.HikariDataSource;
import org.apache.ibatis.session.SqlSessionFactory;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.JdbcTemplate;

import javax.sql.DataSource;

/**
 * adm 库数据源（用户、角色、授权、审计、快照）——<b>主数据源</b>。
 *
 * <p>权限库物理名是 adm 不是 sys：MySQL 自带 sys 库，无法占用。</p>
 */
@Configuration
@MapperScan(basePackages = {"com.generated.platform.admin.mapper", "com.generated.platform.meta"},
            sqlSessionFactoryRef = "admSqlSessionFactory")
            // meta 里是能力视图的只读查询（GrantViewMapper），同走 adm 库
public class AdmDataSourceConfig {

    /** 口令为空启动即炸：db.env 少了 set -a 时会以空口令静默重试，报错出现在
     * 深处的 SQL 层极难定位（README 踩坑记录在案）——与 SecretBox 对 APP_SECRET 同一口径 */
    public AdmDataSourceConfig(@Value("${spring.datasource.adm.password:}") String admPassword) {
        if (admPassword == null || admPassword.isBlank()) {
            throw new IllegalStateException(
                    "缺少 APP_PWD（adm 库口令为空）：请 set -a 后再 source db.env，拒绝以空口令连接");
        }
    }

    @Primary
    @Bean
    @ConfigurationProperties("spring.datasource.adm")
    public DataSource admDataSource() {
        return new HikariDataSource();
    }

    @Primary
    @Bean
    public SqlSessionFactory admSqlSessionFactory(@Qualifier("admDataSource") DataSource dataSource) throws Exception {
        return MybatisSessionFactories.create(dataSource);
    }

    /** 平台状态的单行更新用得上；两个数据源都不在时 JdbcTemplate 自动配置也会退出，必须显式给 */
    @Primary
    @Bean
    public JdbcTemplate admJdbcTemplate(@Qualifier("admDataSource") DataSource dataSource) {
        return new JdbcTemplate(dataSource);
    }
}
