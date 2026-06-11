import os
import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from PIL import Image

def load_metadata(meta_dir):
    data = []
    if not os.path.exists(meta_dir):
        print(f"Thư mục {meta_dir} không tồn tại.")
        return pd.DataFrame()
        
    for filename in os.listdir(meta_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(meta_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    # Flatten dictionary
                    flat_meta = {
                        "product_id": meta.get("product_id"),
                        "product_name": meta.get("product_name"),
                        "source": meta.get("source"),
                        "category_group": meta.get("category_group"),
                        "price": meta.get("attributes", {}).get("price", 0)
                    }
                    data.append(flat_meta)
            except Exception as e:
                print(f"Lỗi khi đọc file {filename}: {e}")
    return pd.DataFrame(data)

def run_eda():
    base_dir = "ssc_data_lake/bronze"
    meta_dir = os.path.join(base_dir, "metadata")
    img_dir = os.path.join(base_dir, "images")
    
    print("="*50)
    print("BẮT ĐẦU EXPLORATORY DATA ANALYSIS (EDA)")
    print("="*50)
    
    print("1. Đang nạp dữ liệu metadata...")
    df = load_metadata(meta_dir)
    
    if df.empty:
        print("Không có dữ liệu để phân tích.")
        return
        
    print(f"-> Đã nạp thành công {len(df)} sản phẩm.\n")
    
    print("2. Xem 5 dòng dữ liệu đầu tiên:")
    print(df.head(), "\n")
    
    print("3. Thông tin thống kê tổng quan cho trường Giá (Price):")
    # Tắt hiển thị scientific notation
    pd.set_option('display.float_format', lambda x: '%.2f' % x)
    print(df['price'].describe(), "\n")
    
    print("4. Số lượng sản phẩm theo từng nguồn (source):")
    print(df['source'].value_counts(), "\n")
    
    print("5. Số lượng sản phẩm theo danh mục (category_group) - Top 10:")
    print(df['category_group'].value_counts().head(10), "\n")
    
    # Tạo thư mục lưu biểu đồ
    output_dir = "eda_results"
    os.makedirs(output_dir, exist_ok=True)
    
    # Vẽ và lưu biểu đồ phân bố giá
    plt.figure(figsize=(10, 6))
    # Loại bỏ 5% giá cao nhất để biểu đồ histogram không bị nhiễu do outliers
    q95 = df['price'].quantile(0.95)
    sns.histplot(df[df['price'] < q95]['price'], bins=50, kde=True)
    plt.title('Phân bố giá sản phẩm (Loại bỏ 5% giá trị cao nhất - Outliers)')
    plt.xlabel('Giá (VNĐ)')
    plt.ylabel('Số lượng')
    # Format trục x
    plt.ticklabel_format(style='plain', axis='x')
    plt.savefig(os.path.join(output_dir, 'price_distribution.png'))
    plt.close()
    print(f"-> Đã lưu biểu đồ phân bố giá tại '{output_dir}/price_distribution.png'")
    
    # Vẽ và lưu biểu đồ số lượng sản phẩm theo danh mục
    plt.figure(figsize=(12, 6))
    top_categories = df['category_group'].value_counts().head(10)
    sns.barplot(x=top_categories.values, y=top_categories.index, palette="viridis")
    plt.title('Top 10 danh mục có nhiều sản phẩm nhất')
    plt.xlabel('Số lượng')
    plt.ylabel('Danh mục')
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'top_categories.png'))
    plt.close()
    print(f"-> Đã lưu biểu đồ danh mục tại '{output_dir}/top_categories.png'")
    
    print("\n="*50)
    print("EDA HOÀN TẤT. BẠN CÓ THỂ KIỂM TRA THƯ MỤC 'eda_results' ĐỂ XEM BIỂU ĐỒ.")
    print("="*50)

if __name__ == "__main__":
    run_eda()
