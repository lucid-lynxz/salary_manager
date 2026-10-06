# !/usr/bin/env python
# -*- coding:utf-8 -*-

import email.encoders
import imaplib
import os.path
import re
import smtplib
import traceback
from base64 import b64decode
from email import message_from_bytes
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr, formataddr
from typing import TypedDict

from util.CommonUtil import CommonUtil
from util.TimeUtil import TimeUtil


class MailBean(object):
    msg_id: str = ''  # 邮件id
    subject: str = ''  # 邮件主题
    sender: str = ''  # 发件人
    receiver: str = ''  # 收件人
    date: str = ''  # 日期
    content: str = ''  # 邮件内容


class MailConfig(TypedDict):
    senderEmail: str  # 发件人邮件地址（必填）
    receiverEmail: str  # 发送邮件时, 默认收件人邮箱地址（可选）
    subject: str  # 发送邮件时,默认的邮件主题信息
    senderPwd: str  # 密码/授权码（必填）
    smtpServer: str  # SMTP服务器地址（可选） 发送邮件时需配置
    smtpPort: int  # SMTP服务器端口（可选）
    imapServer: str  # IMAP服务器地址（可选） 收取邮件时需配置
    imapPort: int  # IMAP服务器端口（可选）
    debugLevel: int  # 调试级别（可选） 1-打印所有日志 0-不打印日志


class MailUtil(object):
    """
    smtp发送邮件工具类
    参考: https://www.liaoxuefeng.com/wiki/1016959663602400/1017790702398272
    https://wx.mail.qq.com/list/readtemplate?name=app_intro.html#/agreement/authorizationCode
    1. 通过 __init__ 方法传入发件人信息,包括发件邮件地址,密码,smtp服务器信息等
    2. 通过 composeTextMsg 方法构造简单的文本内容
    3. 发送邮件: sendTo
    4. 关闭服务器链接: quit
    5. 监听新邮件并读取内容: listen_and_read_new_mails

    可以在配置文件中定义如下section信息:
    [mail]
    # 以下是发送邮箱所需,若无需发送邮件,则可不配置
    # 发送邮件的服务器, 默认使用qq邮箱,其对应端口号通常是587或465
    smtpServer = smtp.qq.com
    smtpPort = 587

    # 接收邮件的服务器和端口号
    imapServer = imap.qq.com
    imapPort = 993

    # 发件人邮箱地址, 如: 66666@qq.com, 也是使用imap服务器读取邮件的地址
    senderEmail =

    # 邮箱密码或者授权码
    senderPwd =

    # 默认的邮件接收者, 比如8888@qq.com
    receiverEmail =

    # 邮件主题内容, 会在后面拼接 "-{姓名}"
    subject =

    # 打印调试信息 1-显示所有smtp交互日志  0-不打印
    debugLevel = 1
    """

    def __init__(self, config: MailConfig):
        """
        :param config: 邮件配置字典
                      必选键: address, pwd
                      可选键: smtpServer, smtpPort, imapServer, imapPort, debugLevel
                  address:  发件人邮件地址
                  pwd: 密码, qq邮箱使用的授权码,文档: https://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
                  smtpServer: smtp服务器地址, qq邮箱是: smtp.qq.com,  文档:https://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=167
                  smtpPort: smtp服务器端口,qq邮箱的默认端口是 587
                  imapServer: imap服务器地址, qq邮箱是: imap.qq.com
                  imapPort: imap服务器端口, qq邮箱的默认端口是 993
                  debugLevel: 打印调试信息 1-显示所有smtp交互日志
        """
        # 设置默认值
        default_config = {
            "senderEmail": "",
            "senderPwd": "",
            "smtpServer": "smtp.qq.com",
            "smtpPort": 587,
            "imapServer": "imap.qq.com",
            "imapPort": 993,
            "debugLevel": 1,
            "receiverEmail": "",
            "subject": "",
        }
        # 合并配置（用户提供的配置优先于默认配置）
        merged_config = {**default_config, **config}
        CommonUtil.printLog(f'merged_config={merged_config}')

        self.mailConfig = merged_config
        self.fromMail = merged_config['senderEmail']

        # 使用 smtp() 接口可能会报错: Connection unexpectedly closed, 改为: smtp_ssl()
        self.smtpServer = smtplib.SMTP_SSL(merged_config["smtpServer"])
        self.smtpServer.set_debuglevel(int(merged_config["debugLevel"]))
        self.smtpServer.login(merged_config['senderEmail'], merged_config['senderPwd'])
        self.mineMsg: MIMEMultipart = MIMEMultipart()  # 邮件对象

        self.attachCnt: int = 0  # 附件个数
        self.imapServer = imaplib.IMAP4_SSL(merged_config["imapServer"], merged_config["imapPort"])

        self.imapServer.login(merged_config['senderEmail'], merged_config['senderPwd'])
        self.default_receiver = merged_config['receiverEmail']
        self.default_subject = merged_config['subject']

    @staticmethod
    def _format_addr(s: str):
        """
        格式化一个邮件地址。
        注意 MIMEText 不能简单地传入 name <addr@example.com>，因为如果包含中文，需要通过Header对象进行编码。
        :param s:
        :return:
        """
        name, addr = parseaddr(s)
        return formataddr((Header(name, 'utf-8').encode(), addr))

    def obtainMailMsg(self, forceRecreate: bool = False) -> MIMEMultipart:
        """
        获取一个 MIMEMultipart 对象
        :param forceRecreate:  是否强制重建
        :return:
        """
        if forceRecreate:
            self.mineMsg = MIMEMultipart()
            self.attachCnt = 0  # 附件个数
        return self.mineMsg

    def setMailMsg(self, content: str, fromTip: str = None, subject: str = None) -> MIMEMultipart:
        """
        构造纯邮件内容
        :param content: 邮件正文,支持html格式
                若需要插入图片到正文,请先添加附件 addAttachFile 后,得到 id, 然后插入 image 标签: <img src="cid:%s"%attachId>
        :param fromTip: 发件人信息,默认使用 self.fromMail, 可自行指定,注意邮件地址需要用尖括号括起来, 如: 测试 <4456***@qq.com>
        :param subject: 邮件主题
        :return:
        """
        self.obtainMailMsg(False)
        self.mineMsg['From'] = MailUtil._format_addr(fromTip if fromTip is not None else self.fromMail)
        if CommonUtil.isNoneOrBlank(subject):
            subject = self.default_subject

        if subject is not None:  # 主题
            self.mineMsg['Subject'] = Header(subject, 'utf-8').encode()
        self.mineMsg.attach(MIMEText(content, 'html', 'utf-8'))  # 邮件正文是MIMEText:
        return self.mineMsg

    def addAttachFile(self, localPath: str, fileType: str = "file") -> int:
        """
        添加附件到邮件中
        :param localPath:本地文件地址
        :param fileType:文件类型,默认为文件(file,可设置为图片等其他格式,如: iamge)
        :return: 当前附件序号 Content-ID, 从0开始
        """
        fileName = os.path.basename(localPath)  # 文件名
        ext = None if "." not in fileName else os.path.splitext(fileName)[-1][1:]  # 文件扩展名

        self.obtainMailMsg(False)
        with open(localPath, 'rb') as f:
            # 设置附件的MIME和文件名
            mime = MIMEBase(fileType, ext, filename=fileName)
            # 加上必要的头信息:
            mime.add_header('Content-Disposition', 'attachment', filename=fileName)
            mime.add_header('Content-ID', '<%s>' % self.attachCnt)
            mime.add_header('X-Attachment-Id', '%s' % self.attachCnt)
            mime.set_payload(f.read())  # 把附件的内容读进来:
            email.encoders.encode_base64(mime)  # 用Base64编码:
            self.mineMsg.attach(mime)  # 添加到MIMEMultipart
        curAttachCnt = self.attachCnt
        self.attachCnt = self.attachCnt + 1
        return curAttachCnt

    def _recookMailAddresss(self, mailAddress: str) -> str:
        """
        处理邮箱地址, 删除前后空格及中间空格
        对不符合格式规范的(如不包含 @ 符号), 返回None
        :param mailAddress: 原始邮件地址
        :return: 最终邮件地址, 接收方注意要进行判空
        """
        if CommonUtil.isNoneOrBlank(mailAddress) \
                or '@' not in mailAddress \
                or mailAddress.startswith('@') \
                or mailAddress.endswith('@'):
            return ''
        return "".join(mailAddress.split())

    def sendTo(self, toMails: list = None, toNames: list = None) -> bool:
        """
        发送邮件给指定的收件人
        :param toMails: 收件人邮件列表, 若为空,则使用默认
        :param toNames: 收件人名称列表 要求与 toMails 长度一致, 若为None,这使用 toMails 替代
        :return: 是否发送成功
        """
        # 检查SMTP连接
        self._check_smtp_connection()

        if CommonUtil.isNoneOrBlank(toMails):
            toMails = [self.default_receiver]
        toMails = list(map(self._recookMailAddresss, toMails))
        toNames = toMails if (toNames is None or len(toNames) == 0) else toNames

        lenMails = len(toMails)
        lenNames = len(toNames)

        # 拼接生成收件人信息
        strToList = list()
        try:
            for index in range(lenMails):
                toMail = toMails[index]
                toName = toMail if index >= lenNames else toNames[index]
                strToList.append(self._format_addr('%s <%s>' % (toName, toMail)))
        except Exception as e:
            strToList = toMails
        self.mineMsg['To'] = ','.join(strToList)  # 生成收件人信息, 转换为 str, 逗号分隔

        try:
            self.smtpServer.sendmail(self.fromMail, toMails, self.mineMsg.as_string())
            return True
        except Exception as e:
            traceback.print_exc()
            CommonUtil.printLog(' 发送邮件给 %s 失败, 错误信息: %s' % (toMails, e))
            return False

    def quit(self):
        self.quit_imap()
        self.quit_smtp()

    def quit_imap(self):
        self.imapServer.logout()

    def quit_smtp(self):
        self.smtpServer.quit()

    def list_folders(self, print_log: bool = False) -> list:
        """
        列出所有可用的文件夹
        @return 文件夹名列表
        """
        _result = []
        status, folders = self.imapServer.list()
        if status == 'OK':
            if print_log:
                CommonUtil.printLog("可用文件夹:")
            for folder in folders:
                decode_name = MailUtil.modified_utf7_decode(folder)
                if print_log:
                    CommonUtil.printLog(f"{folder} ---> {decode_name}")
                _result.append(decode_name)
            return _result
        return _result

    def select_folder(self, folder_name: str = 'INBOX') -> bool:
        """
        选择指定的邮件文件夹, 用于对该文件夹进行后续操作,比如读取邮件, 标记邮件等
        """
        # 处理中文文件夹名称 - 使用 modified UTF-7 编码
        if folder_name != 'INBOX':
            # 将 Unicode 字符串转换为 Modified UTF-7 编码
            folder_bytes = folder_name.encode('utf-7').replace(b'+', b'&')
            folder_spec = f'"{folder_bytes.decode("ascii")}"'
            status, _ = self.imapServer.select(folder_spec)
        else:
            status, _ = self.imapServer.select('INBOX')

        if status != 'OK':
            CommonUtil.printLog(f"无法选择文件夹: {folder_name}")
            return False
        else:
            return True

    def fetch_mails(self, folder_name: str = 'INBOX', criteria: str = 'ALL', limit: int = 0) -> list:
        """
        获取指定文件夹中的邮件
        :param folder_name: 文件夹名称，默认为INBOX, 即收件箱
                            以QQ邮箱为例, 假设有个自定义文件夹为:'测试', 实际名称应该是:'其他文件夹/测试',具体可通过 list_folders() 方法查看
        :param criteria: 搜索条件, 默认为 'ALL', 即所有邮件,注意: 此处的'所有' 指邮箱允许获取的邮件,默认应该是30封
                        'UNSEEN': 未读邮件
                        'SEEN': 已读邮件
        :param limit: 获取邮件的最大数量，0 表示不限制
        :return 邮件对象列表, 列表元素类型是: MailBean
        """
        _result = []
        try:
            # 检查IMAP连接
            self._check_imap_connection()

            if not self.select_folder(folder_name):
                return _result

            # 构建搜索条件
            search_criteria = criteria

            # 搜索邮件
            status, messages = self.imapServer.search(None, search_criteria)
            if status == 'OK':
                message_ids = messages[0].split()
                # 反转邮件 ID 列表以获取最近的邮件
                message_ids = message_ids[::-1]
                if limit > 0:
                    message_ids = message_ids[:limit]
                if not message_ids:
                    CommonUtil.printLog(f"文件夹 '{folder_name}' 中没有{criteria}邮件")
                    return _result

                CommonUtil.printLog(f"读取文件夹 '{folder_name}' 中的 {len(message_ids)} 封{criteria}邮件")
                for message_id in message_ids:
                    status, msg_data = self.imapServer.fetch(message_id, '(RFC822)')
                    if status == 'OK':
                        raw_email = msg_data[0][1]
                        msg = message_from_bytes(raw_email)

                        # 解码主题和发件人
                        subject = self._decode_mime_header(msg['Subject'])
                        from_addr = self._decode_mime_header(msg['From'])
                        date = msg['Date']

                        # 解析邮件内容
                        content = self._parse_email_content(msg)

                        bean = MailBean()
                        bean.msg_id = message_id.decode('utf-8')
                        bean.subject = subject
                        bean.sender = from_addr
                        bean.receiver = msg['To']
                        bean.date = date
                        bean.content = content

                        _result.append(bean)
            else:
                CommonUtil.printLog("搜索邮件失败")
        except Exception as e:
            CommonUtil.printLog(f"读取邮件时出错: {e}")
        return _result

    def set_mail_flags(self, message_ids: list, flag: str, folder_name: str = 'INBOX') -> bool:
        """
        修改指定邮件的标记
        :param message_ids: 邮件 ID 列表
        :param flag: 标记类型, 可选值: SEEN, UNSEEN, JUNK, ANSWERED, FLAGGED, DELETED, DRAFT
        :param folder_name: 文件夹名称，默认为INBOX, 即收件箱
                            以QQ邮箱为例, 假设有个自定义文件夹为:'测试', 实际名称应该是:'其他文件夹/测试',具体可通过 list_folders() 方法查看
        :return: 是否成功设置标记
        """
        try:
            if not self.select_folder(folder_name):
                CommonUtil.printLog(f"set_mail_flags fail 无法选择文件夹: {folder_name}")
                return False

            # 根据 flag 参数设置对应的 IMAP 标记
            if flag == 'SEEN':
                imap_flag = '\\Seen'  # 表示邮件已读。当把邮件标记为 SEEN 时，邮件客户端通常会将其显示为已读状态
            elif flag == 'UNSEEN':
                imap_flag = '\\Unseen'  # 表示邮件未读。标记为 UNSEEN 的邮件在客户端会显示为未读状态
            elif flag == 'JUNK':
                imap_flag = '\\Junk'  # 表示邮件是广告或垃圾邮件。部分邮件客户端会把标记为 JUNK 的邮件自动移动到垃圾邮件文件夹
            elif flag == 'ANSWERED':
                imap_flag = '\\Answered'  # 邮件已回复
            elif flag == 'FLAGGED':
                imap_flag = '\\Flagged'  # 表示邮件被标记为重要，不同客户端可能用星标等方式显示
            elif flag == 'DELETED':
                imap_flag = '\\Deleted'  # 表示邮件已被删除，客户端通常会在下次同步时真正删除这些邮件
            elif flag == 'DRAFT':
                imap_flag = '\\Draft'  # 表示邮件是草稿
            else:
                CommonUtil.printLog(f"set_mail_flags 不支持的标记类型: {flag}")
                return False

            # 将邮件 ID 列表转换为逗号分隔的字符串
            message_id_str = ','.join(message_ids)

            # 设置邮件标记
            status, _ = self.imapServer.store(message_id_str, '+FLAGS', imap_flag)
            __success = status == 'OK'
            __tip = "成功" if __success else f"失败 status={status}"
            CommonUtil.printLog(f'set_mail_flags({message_id_str}, {flag}, {folder_name}) {__tip}')
            return __success
        except Exception as e:
            CommonUtil.printLog(f"set_mail_flags 设置邮件标记时出错: {e}")
            return False

    # ... 已有代码 ...
    @staticmethod
    def _parse_email_content(msg):
        """解析邮件内容，处理多部分邮件"""
        content = ""
        content_type = ""  # 邮件内容类型, 比如 比如 html 或者 plainText
        charset = None  # 邮件内容编码

        # 检查邮件是否为多部分
        if msg.is_multipart():
            # 遍历所有部分
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))

                try:
                    charset = part.get_content_charset() or 'utf-8'
                except:
                    charset = 'utf-8'

                # 跳过附件
                if 'attachment' in content_disposition:
                    continue

                # 处理文本部分
                if content_type in ['text/plain', 'text/html']:
                    try:
                        part_content = part.get_payload(decode=True).decode(charset, errors='ignore')
                        if content_type == 'text/plain':
                            content += part_content
                        # 如需同时处理HTML，可添加相应逻辑
                    except Exception as e:
                        CommonUtil.printLog(f"解码邮件部分失败: {e}")
        else:
            # 单部分邮件
            content_type = msg.get_content_type()
            try:
                charset = msg.get_content_charset() or 'utf-8'
                content = msg.get_payload(decode=True).decode(charset, errors='ignore')
            except Exception as e:
                CommonUtil.printLog(f"解码单部分邮件失败: {e}")
        return content.strip()

    @staticmethod
    def _decode_mime_header(header):
        """解码MIME编码的邮件头部"""
        if not header:
            return ""

        from email.header import decode_header
        decoded_parts = []
        for part, charset in decode_header(header):
            if isinstance(part, bytes):
                if charset:
                    try:
                        decoded_parts.append(part.decode(charset))
                    except UnicodeDecodeError:
                        # 尝试其他常见编码
                        try:
                            decoded_parts.append(part.decode('utf-8'))
                        except UnicodeDecodeError:
                            decoded_parts.append(part.decode('gb18030', errors='replace'))
                else:
                    # 没有指定编码时，尝试使用utf-8
                    try:
                        decoded_parts.append(part.decode('utf-8'))
                    except UnicodeDecodeError:
                        decoded_parts.append(part.decode('gb18030', errors='replace'))
            else:
                decoded_parts.append(part)

        return "".join(decoded_parts)

    @staticmethod
    def modified_utf7_decode(s):
        """
        将修改后的 UTF-7 编码字符串转换为普通的 Unicode 字符串。这种编码格式常见于 IMAP 协议中，特别是在处理包含非 ASCII 字符的文件夹名称时
        """

        if isinstance(s, bytes):
            s = s.decode('ascii', errors='ignore')

        def decode_part(match):
            encoded = match.group(1)
            padding = (4 - len(encoded) % 4) % 4
            encoded += '=' * padding
            decoded = b64decode(encoded)
            return decoded.decode('utf-16be')

        return re.sub(r'&([^&-]*?)-', decode_part, s)

    def _check_smtp_connection(self):
        """检查SMTP连接是否有效，如果无效则重新连接"""
        try:
            # 尝试发送NOOP命令检查连接
            self.smtpServer.noop()
        except:
            # 连接已断开，重新连接
            CommonUtil.printLog("SMTP连接已断开，重新连接中...")
            self.smtpServer = smtplib.SMTP_SSL(self.mailConfig["smtpServer"])
            self.smtpServer.set_debuglevel(int(self.mailConfig["debugLevel"]))
            self.smtpServer.login(self.mailConfig['senderEmail'], self.mailConfig['senderPwd'])

    def _check_imap_connection(self):
        """检查IMAP连接是否有效，如果无效则重新连接"""
        try:
            # 尝试检查连接状态
            self.imapServer.check()
        except:
            # 连接已断开，重新连接
            CommonUtil.printLog("IMAP连接已断开，重新连接中...")
            self.imapServer = imaplib.IMAP4_SSL(self.mailConfig["imapServer"], self.mailConfig["imapPort"])
            self.imapServer.login(self.mailConfig['senderEmail'], self.mailConfig['senderPwd'])


if __name__ == "__main__":
    mail_config = {
        "senderEmail": "4456xxxx@qq.com",
        "senderPwd": "kgxpeoiqzaspxxx",
        "smtpServer": "smtp.qq.com",
        "smtpPort": 587,
        "imapServer": "imap.qq.com",
        "imapPort": 993,
        "debugLevel": 0,
        "receiverEmail": "3605xxx@qq.com",
        "subject": "测试config_dict发送邮件",
    }
    mailUtil = MailUtil(mail_config)

    # 普通文本正文
    # msg = mailUtil.setMailMsg("你好,hello from python", "我是4456 <%s>" % mailUtil.fromMail, "测试python2")
    # msg = mailUtil.setMailMsg("你好,hello2", "我是4456 <%s>" % mailUtil.fromMail)

    # 正文中插入图片, 测试后,该文件不会再显示再附件中(qq邮箱)
    # 1. 添加图片附件
    cid = mailUtil.addAttachFile("H:/Pictures/壁纸/hello.jpg", "image")
    # 2. 插入image标签
    mailUtil.setMailMsg('<html><body><h1>你好,hello from python</h1>' +
                        '<p><img src="cid:%s"></p>' % cid +
                        '</body></html>', "我是4456 <%s>" % mailUtil.fromMail, "测试python")

    # # 添加普通文件附件
    # # mailUtil.addAttachFile("/Users/***/Desktop/test.py", "file")
    # 发送到指定邮箱
    # senderrs = mailUtil.sendTo(["360***@qq.com"])
    senderrs = mailUtil.sendTo()

    # # 列出所有文件夹
    # CommonUtil.printLog(f'{mailUtil.list_folders()}')
    #
    # # # 读取指定文件夹中的邮件
    # # # mail_list = mailUtil.fetch_mails("其他文件夹/尊嘉",'UNSEEN')
    # mail_list = mailUtil.fetch_mails('INBOX', 'UNSEEN', limit=0)
    # for mail in mail_list:
    #     CommonUtil.printLog('')
    #     CommonUtil.printLog(f'邮件ID: {mail.msg_id}')
    #     CommonUtil.printLog(f'主题: {mail.subject}')
    #     CommonUtil.printLog(f'发件人: {mail.sender}')
    #     CommonUtil.printLog(f'日期: {mail.date}')
    #     # CommonUtil.printLog(f'内容: {mail.content}')
    #     CommonUtil.printLog('-----------------')
    #     _success = mailUtil.set_mail_flags([mail.msg_id], 'SEEN', 'INBOX')
    #     CommonUtil.printLog(f'设置邮件ID: {mail.msg_id} 为已读={_success}')

    mailUtil.quit()
