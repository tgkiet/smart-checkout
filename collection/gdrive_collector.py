import io
import os
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Định nghĩa quyền truy cập (Chỉ đọc)
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')

CREDENTIALS_PATH = "/home/qk/Documents/QUOCKHANH/smart-checkout/config/credentials.json"
TOKEN_PATH = "/home/qk/Documents/QUOCKHANH/smart-checkout/config/token.json"
class GDriveCollector:    
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
            
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())
                
        return build('drive', 'v3', credentials=creds)

    def _download_folder_stream(self, folder_id, output_dir, folder_name):
        """Vừa quét vừa tải file về (tiết kiệm bộ nhớ và thấy tiến độ ngay)"""
        import json
        os.makedirs(output_dir, exist_ok=True)
        state_file = os.path.join(output_dir, f"state_{folder_name}.json")
        
        # Đọc state cũ
        processed_files = []
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    processed_files = json.load(f).get("processed", [])
            except Exception:
                pass
                
        page_token = None
        total_scanned = 0
        success = 0
        skipped = 0
        
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
                total_scanned += 1
                file_name = file['name']
                file_id = file['id']
                
                if file_name in processed_files:
                    skipped += 1
                    continue
                    
                logging.info(f"[{folder_name}] Đang tải file {total_scanned}: {file_name}")
                
                if self._download_file(file_id, file_name, output_dir):
                    success += 1
                
                processed_files.append(file_name)
                
                # Cứ 10 file thì lưu state
                if total_scanned % 10 == 0:
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump({"processed": processed_files}, f, ensure_ascii=False, indent=4)
                        
            # Lưu state ở cuối mỗi trang
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump({"processed": processed_files}, f, ensure_ascii=False, indent=4)
                
            page_token = results.get('nextPageToken', None)
            if page_token is None:
                break
                
        logging.info("="*50)
        logging.info(f"BÁO CÁO THƯ MỤC {folder_name}:")
        logging.info(f" - Tải mới: {success} file")
        logging.info(f" - Bỏ qua: {skipped} file")
        logging.info("="*50)

    def _download_file(self, file_id, file_name, destination_folder):
        """Tải một file từ Google Drive về máy"""
        os.makedirs(destination_folder, exist_ok=True)
        file_path = os.path.join(destination_folder, file_name)
        
        # Bỏ qua nếu file đã tồn tại
        if os.path.exists(file_path):
            return False
            
        request = self.service.files().get_media(fileId=file_id)
        fh = io.FileIO(file_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            
        return True

    def collect_data(self, products_folder_id, images_folder_id, output_dir="ssc_data_lake/bronze"):
        """Tải dữ liệu từ Google Drive (products và images)"""
        logging.info("🚀 Bắt đầu lấy dữ liệu trực tiếp từ Google Drive...")
        
        products_out = os.path.join(output_dir, "metadata")
        images_out = os.path.join(output_dir, "images")
        
        logging.info(f"--- ĐANG XỬ LÝ THƯ MỤC PRODUCTS ---")
        self._download_folder_stream(products_folder_id, products_out, "products")
        
        logging.info(f"--- ĐANG XỬ LÝ THƯ MỤC IMAGES ---")
        self._download_folder_stream(images_folder_id, images_out, "images")
        
        logging.info("✅ HOÀN TẤT TOÀN BỘ QUÁ TRÌNH TẢI DATA!")

if __name__ == '__main__':
    # Chú ý: Bạn lấy ID của thư mục bằng cách mở thư mục trên Google Drive và copy đoạn mã ở cuối URL
    PRODUCTS_FOLDER_ID = '1ZSX2-NV5v1HXOurS_ZdzMOEB7TxM7t75' 
    IMAGES_FOLDER_ID = '1LylxqqVlKA20zgFo0I559oH6NQmrYF7u'
    
    collector = GDriveCollector()
    collector.collect_data(PRODUCTS_FOLDER_ID, IMAGES_FOLDER_ID)