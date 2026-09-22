@echo off
REM 凭据来源：环境变量 QQ_BOT_APPID / QQ_BOT_SECRET 优先，
REM 其次回退读 liz_bot\config\config.yaml。详见 README.md 的「配置」一节。
cd /d %~dp0
python run.py
