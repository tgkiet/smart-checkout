import io
import os
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CREDENTIALS_PATH = "/home/quockhanh/smart-checkout/config/credentials.json"
TOKEN_PATH = "/home/quockhanh/smart-checkout/config/token.json"

class GDriveHandler:   
    def __init__(self, credentials_path=CREDENTIALS_PATH, token_path=TOKEN_PATH):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()

    def _authenticate(self):
        """Xác thực với Google Drive API"""
        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Đảm bảo thư mục tồn tại
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())
                
        return build('drive', 'v3', credentials=creds)

    def list_files(self, folder_id):
        """Yield danh sách files từ thư mục, hỗ trợ phân trang."""
        page_token = None
        while True:
            results = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces='drive',
                pageSize=1000,
                fields="nextPageToken, files(id, name)",
                pageToken=page_token
            ).execute()
            
            chunk = results.get('files', [])
            for file in chunk:
                yield file
                
            page_token = results.get('nextPageToken', None)
            if page_token is None:
                break

    def find_file_by_name(self, folder_id, file_name):
        """Tìm file trong thư mục theo tên."""
        safe_name = file_name.replace("'", "\\'")
        query = f"'{folder_id}' in parents and name='{safe_name}' and trashed=false"
        results = self.service.files().list(
            q=query,
            spaces='drive',
            pageSize=1,
            fields="files(id, name)"
        ).execute()
        files = results.get('files', [])
        return files[0] if files else None

    def download_file_content(self, file_id):
        """Tải nội dung file dưới dạng byte vào bộ nhớ (không lưu ra đĩa)."""
        request = self.service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            
        fh.seek(0)
        return fh.read()
