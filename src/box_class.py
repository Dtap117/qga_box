# encoding: utf-8

import os
import io
import json
import time
import base64
import argparse
import subprocess
import configparser
import logging.handlers


CFG_INFO = os.getcwd() + "/cfg.conf"
CFG = configparser.ConfigParser()
CFG.read(CFG_INFO, encoding='utf-8')
LEVELS = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR
}
LOG_BACKUP_COUNT = 5
LOG_LEVEL = 'debug'


class PubClass:
    @staticmethod
    def clear_params(params):
        for key in list(params.keys()):
            if not params.get(key):
                del params[key]

    @staticmethod
    def get_logger(log_file=None, thread_name=None):
        logger = None
        if not log_file:
            log_file = CFG.get("log", "log_file")
        if not thread_name:
            thread_name = CFG.get("log", "thread_name")
        try:
            logger = logging.getLogger(thread_name)
            handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=1024 * 1000, backupCount=LOG_BACKUP_COUNT)
            logger.addHandler(handler)
            logger.setLevel(LEVELS.get(LOG_LEVEL))
        except Exception as ex_info:
            logging.info(ex_info)
        return logger

    @staticmethod
    def check_json(params):
        try:
            json.loads(params)
            return True
        except Exception as ex_info:
            logging.info(ex_info)
            return False


class ExecClass:
    @staticmethod
    def base_cmd(command, env_info=None):
        if env_info:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=-1,
                env=env_info
            )
        else:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
        proc.wait()
        proc_stdout = io.TextIOWrapper(proc.stdout, encoding='utf-8').read().rstrip()
        proc_stderr = io.TextIOWrapper(proc.stderr, encoding='utf-8').read().rstrip()
        return proc_stdout, proc_stderr


class KvmClass(ExecClass, PubClass):
    def __init__(self, repeat_time):
        self.LOGGER = self.get_logger()
        self.command_qga = 'virsh qemu-agent-command %s --cmd'
        self.command_qga_check_service = self.command_qga + ' \'{"execute":"guest-ping"}\''
        self.repeat_time = repeat_time
        ExecClass.__init__(self)
        PubClass.__init__(self)

    @staticmethod
    def get_while_mark(repeat_type, exec_res):
        if repeat_type == "exited":
            while_mark = exec_res['return']['exited']
        else:
            while_mark = repeat_type in exec_res
        return while_mark

    def repeat_communicate(self, cmd, repeat_count=None, repeat_type="exited"):
        if not repeat_count:
            repeat_count = self.repeat_count
        i = 0
        exec_res, error = self.base_cmd(cmd)
        while not self.check_json(exec_res):
            time.sleep(self.repeat_time)
            exec_res, error = self.base_cmd(cmd)
            if i > repeat_count:
                return "repeat out"
            i += 1
            self.LOGGER.info("repeat count %s: {stdout: '%s', stderr: '%s'}" % (i, exec_res, error))

        logging.info(f"repeat_communicate exec_res: {exec_res}")
        exec_res = json.loads(exec_res)
        self.LOGGER.info("cmd_res: {stdout: '%s', stderr: '%s'}" % (exec_res, error))
        while_mark = self.get_while_mark(repeat_type, exec_res)
        while not while_mark:
            time.sleep(self.repeat_time)
            exec_res, error = self.base_cmd(cmd)
            exec_res = json.loads(exec_res)
            while_mark = self.get_while_mark(repeat_type, exec_res)
            if i > repeat_count:
                return "repeat out"
            i += 1
            self.LOGGER.info("retry count %s: {stdout: '%s', stderr: '%s'}" % (i, exec_res, error))
        return exec_res

    def qga_cmd(self, vm_id, cmd, params):
        exec_params = {
            "path": cmd,
            "arg": params,
            "capture-output": True
        }
        self.clear_params(exec_params)
        res_str = self.command_qga % vm_id
        res_str += ' \'{"execute": "guest-exec", "arguments": %s}\'' % json.dumps(exec_params)
        return res_str

    def qga_make_get_pid_res_cmd(self, pid, vm_id):
        exec_params = {"pid": pid}
        self.clear_params(exec_params)
        res_str = self.command_qga % vm_id
        res_str += ' \'{"execute": "guest-exec-status", "arguments": %s}\'' % json.dumps(exec_params)
        return res_str

    @staticmethod
    def qga_res_decode(exec_status_res):
        try:
            if 'exitcode' in exec_status_res['return'] and exec_status_res['return']['exitcode'] == 0:
                if 'out-data' in exec_status_res['return']:
                    return base64.b64decode(exec_status_res['return']['out-data'])
                else:
                    return exec_status_res
            elif 'err-data' in exec_status_res['return']:
                return base64.b64decode(exec_status_res['return']['err-data'])
            else:
                return exec_status_res
        except Exception as ex_info:
            logging.info(ex_info)
            return exec_status_res

    def qga_command(self, vm_id, cmd_line, params):
        exec_str = self.qga_cmd(vm_id, cmd_line, params)
        self.LOGGER.info("qga_exec_str: %s" % exec_str)

        exec_pid_res, error = self.base_cmd(exec_str)
        self.LOGGER.info("exec_pid_res: {stdout: '%s', stderr: '%s'}" % (exec_pid_res, error))

        try:
            exec_pid_res = json.loads(exec_pid_res)
        except Exception as ex:
            logging.info(ex)
            return error, 500
        exec_res_cmd = self.qga_make_get_pid_res_cmd(exec_pid_res['return']['pid'], vm_id)
        self.LOGGER.info("exec_res_cmd: %s" % exec_res_cmd)

        exec_res = self.repeat_communicate(exec_res_cmd)
        self.LOGGER.info("exec_res: {stdout: '%s', stderr: '%s'}" % (exec_res, error))
        out_str = self.qga_res_decode(exec_res)
        self.LOGGER.info("qga_res_decode: %s" % out_str)
        return exec_res, exec_res.get("return").get("exitcode")

    def qga_check_service(self, vm_id):
        cmd = self.command_qga_check_service % vm_id
        return self.base_cmd(cmd)

    def virsh_exec(self, command, env_info=None):
        return self.base_cmd(command, env_info)

    def check_service(self, vm_id):
        command_str = self.command_qga % vm_id
        command_str += '\'{"execute": "guest-ping"}\''
        return command_str


class Business(KvmClass):
    def __init__(self, vm_id, repeat_time):
        self.vm_id = vm_id
        KvmClass.__init__(self, repeat_time=repeat_time)

    def biz_command(self, cmd_line, params=None):
        if params:
            params_info = params.split(",")
        else:
            params_info = None
        return self.qga_command(self.vm_id, cmd_line, params_info)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Example with long option names")
    parser.add_argument('vm_id', help="虚拟机ID")
    parser.add_argument('--cmd_line', help="执行命令")
    parser.add_argument('--params', help="命令参数")
    parser_params = parser.parse_args()
    business_obj = Business(parser_params.vm_id, CFG.get("command", "repeat_count"))
    business_obj.LOGGER.info("params: %s" % parser_params)
    res, exit_code = business_obj.biz_command(parser_params.cmd_line, parser_params.params)
    logging.info(res)
