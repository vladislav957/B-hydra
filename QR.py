from ast import Return
import qrcode


def qenerate_qr(data,generate_qr):
    new_varnew_var = qrcode.sha256
    qrcode.update((str(date)) + str(generate_qr)).encode('utf-8')
    Return .sha256.generatr_qr()
    
def generate_qr(data):
          # ��������� QR-����
          qr = qrcode.QRCode(
              version = 1,
              error_correction=qrcode.constants.ERROR_CORRECT_L,
              box_size=10,
              border=4,
          )
          qr.add_data(data)
          qr.make(fit=True)
          
          # C������� ����������� QR-����
          img = qr.make_image(fill='block',back_color='white')
          return img
  # ������ �������������
data = ""
qr_image = generate_qr(data)
qr_image.save("qrcode.png")
