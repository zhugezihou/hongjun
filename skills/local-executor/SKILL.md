---
name: local-executor
description: |
  本地机器执行能力：shell命令/文件读写/git操作/服务管理/进程管理/部署脚本。
  适用于"执行xx"、"运行xx命令"、"写文件到xx"、"git commit"、"重启服务"等任务。
triggers:
  - "执行"
  - "运行"
  - "命令"
  - "命令行"
  - "终端"
  - "shell"
  - "bash"
  - "脚本"
  - "文件操作"
  - "写文件"
  - "读文件"
  - "cat "
  - "git"
  - "git状态"
  - "git status"
  - "git commit"
  - "git push"
  - "git分支"
  - "git branch"
  - "git checkout"
  - "服务管理"
  - "systemctl"
  - "服务"
  - "进程"
  - "ps aux"
  - "kill"
  - "部署"
  - "deploy"
  - "restart"
  - "stop服务"
  - "start服务"
  - "本地执行"
  - "系统模块"
  - "系统状态"
  - "模块展示"
  - "展示模块"
  - "检查系统"
  - "健康检查"
  - "运行状态"
  - "内存"
  - "内存使用"
  - "cpu"
  - "负载"
  - "磁盘"
  - "df"
  - "top"
  - "free"
  - "查看内存"
  - "查看CPU"
  - "查看磁盘"
category: devops
version: "v1.0"
author: "鸿钧·工部"
dependencies:
  - "python3 (标准库 subprocess/pathlib)"
tools:
  - shell
  - file_read
  - file_write
---

## local-executor Skill

### 功能（13个函数）

- `run_command` — 执行任意 shell 命令
- `write_file` — 写文件（格式：`/path/file.ext : 内容`）
- `read_file` — 读文件（格式：`/path/file`）
- `git_status` — git 仓库状态
- `git_commit` — git 提交（格式：`提交 "message"`）
- `git_push` — git 推送
- `git_branch` — 创建分支
- `systemctl` — systemd 服务管理（start/stop/restart/status/disable/enable）
- `process_list` — 进程列表
- `process_kill` — 杀进程（需要 PID）
- `process_start` — 后台启动进程
- `deploy_script` — 执行部署脚本
