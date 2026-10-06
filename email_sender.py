"""
邮件发送调度器：支持多账号并发、速率限制和负载均衡。
"""
import sys
import time
import threading
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from util.MailUtil import MailUtil


class EmailSender:
    """邮件发送调度器，管理多个发件人账号的并发发送。"""
    
    def __init__(self, get_db_func, decrypt_func):
        """
        初始化调度器。
        
        Args:
            get_db_func: 获取数据库连接的函数
            decrypt_func: 解密密码的函数
        """
        self.get_db = get_db_func
        self.decrypt_password = decrypt_func
        self.lock = threading.Lock()
        
    def _get_enabled_senders(self) -> List[Dict]:
        """获取所有启用的发件人账号。"""
        with self.get_db() as conn:
            rows = conn.execute("""
                SELECT id, email, password_encrypted, smtp_server, smtp_port,
                       imap_server, imap_port, send_interval, last_send_time
                FROM sender_emails
                WHERE is_enabled = 1
                ORDER BY last_send_time ASC NULLS FIRST
            """).fetchall()
            
            senders = []
            for row in rows:
                senders.append({
                    "id": row["id"],
                    "email": row["email"],
                    "password": self.decrypt_password(row["password_encrypted"]),
                    "smtp_server": row["smtp_server"],
                    "smtp_port": row["smtp_port"],
                    "imap_server": row["imap_server"],
                    "imap_port": row["imap_port"],
                    "send_interval": row["send_interval"],
                    "last_send_time": row["last_send_time"]
                })
            
            return senders
    
    def _can_send(self, sender: Dict) -> bool:
        """检查发件人是否可以发送邮件（是否超过间隔时间）。"""
        if not sender["last_send_time"]:
            return True
        
        last_time = datetime.fromisoformat(sender["last_send_time"])
        now = datetime.now()
        elapsed = (now - last_time).total_seconds()
        
        return elapsed >= sender["send_interval"]
    
    def _update_last_send_time(self, sender_id: int):
        """更新发件人的最后发送时间。"""
        with self.get_db() as conn:
            conn.execute("""
                UPDATE sender_emails 
                SET last_send_time = datetime('now', 'localtime'),
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
            """, (sender_id,))
    
    def _update_person_status(self, person_id: int, status: str, error_msg: Optional[str] = None, sender_id: Optional[int] = None):
        """更新人员的邮件发送状态。"""
        with self.get_db() as conn:
            if status == "success":
                conn.execute("""
                    UPDATE persons 
                    SET email_sent = 1,
                        email_sent_at = datetime('now', 'localtime'),
                        email_sender_id = ?,
                        email_send_status = 'sent',
                        email_error_msg = NULL
                    WHERE id = ?
                """, (sender_id, person_id))
            else:
                conn.execute("""
                    UPDATE persons 
                    SET email_send_status = ?,
                        email_error_msg = ?
                    WHERE id = ?
                """, (status, error_msg, person_id))
    
    def _send_single_email(self, sender: Dict, recipient_email: str, subject: str, 
                          content: str, person_id: int) -> Dict:
        """
        发送单封邮件。
        
        Returns:
            Dict with keys: success, error, sender_id
        """
        try:
            # 创建邮件配置
            mail_config = {
                "senderEmail": sender["email"],
                "senderPwd": sender["password"],
                "smtpServer": sender["smtp_server"],
                "smtpPort": sender["smtp_port"],
                "imapServer": sender["imap_server"],
                "imapPort": sender["imap_port"],
                "debugLevel": 0,
                "receiverEmail": recipient_email,
                "subject": subject,
            }
            
            # 创建邮件工具实例
            mail_util = MailUtil(mail_config)
            
            # 设置邮件内容
            mail_util.setMailMsg(content, fromTip=f"工资通知 <{sender['email']}>", subject=subject)
            
            # 发送邮件
            success = mail_util.sendTo([recipient_email])
            
            # 关闭连接
            mail_util.quit()
            
            if success:
                # 更新最后发送时间
                self._update_last_send_time(sender["id"])
                
                # 更新人员状态
                self._update_person_status(person_id, "success", sender_id=sender["id"])
                
                return {"success": True, "error": None, "sender_id": sender["id"]}
            else:
                error_msg = "发送失败，未知错误"
                self._update_person_status(person_id, "failed", error_msg=error_msg)
                return {"success": False, "error": error_msg, "sender_id": sender["id"]}
                
        except Exception as e:
            error_msg = str(e)
            print(f"[EMAIL SEND ERROR] {error_msg}", file=sys.stderr, flush=True)
            self._update_person_status(person_id, "failed", error_msg=error_msg)
            return {"success": False, "error": error_msg, "sender_id": sender["id"]}
    
    def send_batch_emails(self, emails_to_send: List[Dict], progress_callback=None) -> Dict:
        """
        批量发送邮件，使用多账号并发和速率限制。
        
        Args:
            emails_to_send: List of dicts with keys:
                - person_id: 人员ID
                - email: 收件人邮箱
                - subject: 邮件主题
                - content: 邮件内容（HTML）
            progress_callback: Optional callback function(current, total, result)
        
        Returns:
            Dict with keys: total, success_count, failed_count, results
        """
        if not emails_to_send:
            return {"total": 0, "success_count": 0, "failed_count": 0, "results": []}
        
        # 获取可用的发件人账号
        senders = self._get_enabled_senders()
        if not senders:
            return {
                "total": len(emails_to_send),
                "success_count": 0,
                "failed_count": len(emails_to_send),
                "results": [{"error": "没有可用的发件人账号"} for _ in emails_to_send]
            }
        
        print(f"[EMAIL BATCH] Starting batch send: {len(emails_to_send)} emails, {len(senders)} senders", 
              file=sys.stderr, flush=True)
        
        results = []
        success_count = 0
        failed_count = 0
        current_index = 0
        
        # 使用线程池并发发送，最大并发数等于发件人数量
        max_workers = min(len(senders), 5)  # 最多5个并发线程
        
        def send_with_sender(email_data):
            """为每封邮件选择合适的发件人并发送。"""
            nonlocal current_index
            
            # 等待可用的发件人
            while True:
                with self.lock:
                    # 找到第一个可以发送的发件人
                    for sender in senders:
                        if self._can_send(sender):
                            # 标记该发件人正在使用（通过更新时间防止其他线程使用）
                            self._update_last_send_time(sender["id"])
                            selected_sender = sender.copy()
                            break
                    else:
                        # 没有可用的发件人，等待一下
                        selected_sender = None
                
                if selected_sender:
                    break
                
                # 等待1秒后重试
                time.sleep(1)
            
            # 发送邮件
            result = self._send_single_email(
                selected_sender,
                email_data["email"],
                email_data["subject"],
                email_data["content"],
                email_data["person_id"]
            )
            
            return result
        
        # 使用线程池执行
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            
            # 提交所有任务
            for email_data in emails_to_send:
                future = executor.submit(send_with_sender, email_data)
                futures[future] = email_data
            
            # 收集结果
            for i, future in enumerate(as_completed(futures)):
                email_data = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    if result["success"]:
                        success_count += 1
                    else:
                        failed_count += 1
                    
                    # 调用进度回调
                    if progress_callback:
                        progress_callback(i + 1, len(emails_to_send), result)
                    
                    print(f"[EMAIL BATCH] Progress: {i+1}/{len(emails_to_send)} - {'Success' if result['success'] else 'Failed'}", 
                          file=sys.stderr, flush=True)
                        
                except Exception as e:
                    error_msg = f"线程执行异常: {str(e)}"
                    print(f"[EMAIL BATCH ERROR] {error_msg}", file=sys.stderr, flush=True)
                    results.append({"success": False, "error": error_msg, "sender_id": None})
                    failed_count += 1
                    
                    if progress_callback:
                        progress_callback(i + 1, len(emails_to_send), {"success": False, "error": error_msg})
        
        summary = {
            "total": len(emails_to_send),
            "success_count": success_count,
            "failed_count": failed_count,
            "results": results
        }
        
        print(f"[EMAIL BATCH] Completed: {success_count} succeeded, {failed_count} failed", 
              file=sys.stderr, flush=True)
        
        return summary
