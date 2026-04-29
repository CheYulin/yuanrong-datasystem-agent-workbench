# 模板说明：      
## 1 以故障树的形式组织故障模式，文档整体格式为：     
1. 一级故障            
1.1 二级故障       
1.1.1 三级故障     
1.1.2 三级故障
...   
1.2 二级故障       
1.2.1 三级故障
...

## 2 单个故障模式故障书写说明如下：
x.x... xxx故障模式名称
* 故障现象：
    * case1: 通过日志关键字识别故障现象，需给出
        * 日志入口：日志需要查看的日志文件路径或者查询命令
        * 关键字：在日志中用于识别故障现象的关键字，说明如下：        
            1、如果故障需要多行日志进行定位，给出每行日志待识别的关键字以及组合关系说明，例如需要满足关键字1，关键字2，关键字3才为此故障，或者匹配关键字1，关键字2，关键字3中一个即为此故障     
            2、可以给出具体日志示例（文本），用于辅助理解故障现象的判定标准。   
    * case 2：通过调用命令行并识别关键字识别故障现象，需给出
        * 执行命令行：需给出具体命令和参数。
        * 关键字：在命令行执行结果中需要识别的关键字，同case 1
    * case 3：故障现象需要经过多次日志和命令行调用进行组合逻辑判断，需说明详细步骤和步骤间的逻辑关系，每个步骤的说明规范见case 1和case 2,。
    * case 4：故障无具体现象，仅是逻辑上的故障分类，可不写，只列出故障名称
* 故障原因：
    * case 1：故障模式对应具体故障原因，需进行说明。
    * case 2：如故障还需向下级故障匹配，无法定位到根因可不写。
* 解决办法：
    * case 1：故障模式对应具体解决方法，需进行说明。
    * case 2：如故障还需向下级故障匹配，可不写。

# 示例

## 文档：KVCache定位定界故障模式树
### 1 KVCache中断异常
* 故障现象：
日志入口：查询KVCache错误码：grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log \   | awk -F'|' '{print $1}' | sort | uniq -c
关键字：返回错误码非空
* 故障原因：向下级匹配。
* 解决方法：向下级匹配。
### 1.1 错误码K_RUNTIME_ERROR
* 故障现象：
日志入口：查询KVCache错误码：grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log \   | awk -F'|' '{print $1}' | sort | uniq -c
关键字：返回错误码中有K_RUNTIME_ERROR
本地KVCache查询命令/路径：grep -E 'K_RUNTIME_ERROR|Get mmap entry failed|etcd is|urma' $LOG/datasystem_worker.INFO.log | tail -50
关键字：K_RUNTIME_ERROR
* 故障原因：
向下级匹配
* 解决办法：
无
### 1.1.1 Get mmap entry failed
* 故障现象：
日志入口：grep -E 'K_RUNTIME_ERROR|Get mmap entry failed|tail -50
关键字：返回非空
* 故障原因：OS，客户业务运维负责
* 解决方法：无
### 1.1.2 etcd is timeout/unavailable
* 故障现象：
日志入口：grep -E 'etcd is timeout/unavailable|tail -50
关键字：返回非空
* 故障原因：etcd三方，客户中台运维负责
* 解决方法：无
### 1.1.3 urma ... payload ...
* 故障现象：
日志入口：grep -E 'urma' $LOG/datasystem_worker.INFO.log|tail -50
关键字：返回非空
* 故障原因：URMA，分布式并行实验室负责
* 解决方法：无