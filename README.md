# 📧 SMTP Marketplace Telegram Bot

A production-grade Telegram storefront and inventory management bot with **BTC & USDT TRC-20** payments, Fernet-encrypted credentials, verified customer reviews, transparent backorders, and a **100% Visual & Touch-Driven Admin Panel**.

---

## 🌟 Interactive Visual Features

### 1. ➕ Visual Product Creation Wizard (No Slash Commands Needed)
Tap **`➕ Add Product (Wizard)`** inside `/admin`:
- **⚡ 1-Tap Presets**: Add ready-made products with pre-configured titles, regions, and pricing (e.g., *SMTP Random Limit ($450)*, *AWS SES 1M ($1,500)*).
- **✏️ Custom Product**: The bot prompts you directly in chat:
  1. Send Product Title
  2. Send Price in USD
  - Automatically activates the product in Pre-Order / Stock mode.

### 2. 🛠️ Visual Product Editor
Tap any product in **`📦 Products & Pricing`** to access touch controls:
- **`💲 Adjust Price`**: Tap **`+$10`**, **`-$10`**, **`+$50`**, **`-$50`**, **`+$100`**, or **`-$100`** buttons to adjust prices live with one tap, or type an exact custom price.
- **`📥 Add Accounts`**: Tap the button and paste your account credentials directly into chat (1 account or 100 accounts).
- **`⏱️ Delivery ETA`**: Select from preset delivery windows (**`⚡ 1-3 Hours`**, **`🕒 6-12 Hours`**, **`📅 24 Hours`**, **`⏳ 24-48 Hours`**, **`📆 3-5 Days`**).
- **`👁️ Toggle Visibility`**: One tap to hide/show from the storefront.
- **`🟡 Toggle Pre-Orders`**: One tap to allow/disallow backorders when stock is 0.
- **`🗑️ Delete Product`**: Visual confirmation prompt before deleting.

### 3. ⚠️ Visual "Clear All Products" (Wipe Out Inventory)
- In **`📦 Products & Pricing`**, tap **`⚠️ Clear All Products`**.
- A confirmation prompt ensures you don't delete by mistake.
- Tapping **`🔴 Yes, Wipe All Products`** purges all products and unallocated inventory instantly.

### 4. ⭐ Verified Customer Reviews
- Only customers who have completed a purchase and received credentials can submit a review (1 per order).
- 1–5 Star Rating with optional public comments.
- Browsable by all users from the main menu (**`⭐ Customer Reviews`**).

---

## 🚀 Running the Bot

```bash
./run.sh
```
