from cryptography.fernet import Fernet
import os

# 1. Generate a secure key
key = Fernet.generate_key()
print(f"\n=== COPY THIS KEY BELOW TO GITHUB SECRETS (Name: PDF_KEY) ===\n")
print(key.decode())
print(f"\n===========================================================\n")

# 2. Encrypt the PDF
if not os.path.exists("DocSolScantron.pdf"):
    print("Error: DocSolScantron.pdf not found!")
else:
    f = Fernet(key)
    with open("DocSolScantron.pdf", "rb") as file:
        file_data = file.read()

    encrypted_data = f.encrypt(file_data)

    with open("DocSolScantron.enc", "wb") as file:
        file.write(encrypted_data)

    print("Success! Created 'DocSolScantron.enc'. This file is safe to upload.")