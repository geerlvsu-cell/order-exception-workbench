# 订单异常处理工作台

个人练习项目，全部使用虚构模拟数据。公开演示入口为 public_app.py。

打开后自动载入300条模拟订单。支持筛选、异常依据、处理动作与状态、CSV导出和模拟CSV导入。

每位访客独享一个会话内存数据库，处理记录不会共享给其他访客；刷新或关闭会话后重新开始，需要保留时先下载结果。没有真实ERP接口、登录或真实业务数据。

## 本地运行

建议Python 3.12。安装 requirements.txt 后运行：

    python -m streamlit run public_app.py

app.py 是单人本地持久版；公网发布必须选择 public_app.py。

## 验证

    python -m unittest discover -s tests -v

当前43项自动检查通过，含访客隔离、会话重置和交互保存。平台部署和实际外网访问仍须分别核验。

## Streamlit Community Cloud

选择仓库与分支，入口 public_app.py，Python选择3.12。部署后核对公开访问设置，用未登录浏览器和手机网络测试；不可把本地地址写入简历。

源码只含代码与模拟验收样例，不含本机数据库、账号凭据或实际处理数据。
