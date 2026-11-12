package com.generated.platform.admin.config;

import com.zaxxer.hikari.HikariDataSource;
import org.apache.ibatis.session.SqlSessionFactory;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;

import javax.sql.DataSource;

/**
 * biz 库数据源（业务表，模板生成的 Mapper 走这里）。
 *
 * <p>为什么必须分库：MCP 账号要 biz 的 DDL 权限，而 MySQL 的权限粒度是库级——
 * 一个能 CREATE TABLE 的账号同样能 DROP TABLE adm_user。业务表与权限表同库，
 * 等于把整把刀交给生成侧（权限 6 节）。</p>
 */
@Configuration
@MapperScan(basePackages = "com.generated.generated.mapper", sqlSessionFactoryRef = "bizSqlSessionFactory")
public class BizDataSourceConfig {

    /** 与 AdmDataSourceConfig 同一口径：空口令启动即炸，不做静默重试 */
    public BizDataSourceConfig(@Value("${spring.datasource.biz.password:}") String bizPassword) {
        if (bizPassword == null || bizPassword.isBlank()) {
            throw new IllegalStateException(
                    "缺少 APP_PWD（biz 库口令为空）：请 set -a 后再 source db.env，拒绝以空口令连接");
        }
    }

    @Bean
    @ConfigurationProperties("spring.datasource.biz")
    public DataSource bizDataSource() {
        return new HikariDataSource();
    }

    @Bean
    public SqlSessionFactory bizSqlSessionFactory(@Qualifier("bizDataSource") DataSource dataSource) throws Exception {
        return MybatisSessionFactories.create(dataSource);
    }

    /** 结构快照要从 information_schema 读列定义，那里用不上 ORM */
    @Bean
    public JdbcTemplate bizJdbcTemplate(@Qualifier("bizDataSource") DataSource dataSource) {
        return new JdbcTemplate(dataSource);
    }
}
