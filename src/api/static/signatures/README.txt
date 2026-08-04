Email-signature images (served publicly at /static/signatures/).

WHICH images are needed is decided by the signature itself, not by this folder. The
signature HTML lives in the database and is edited in the console (이메일 템플릿 → 서명),
so whatever <img src="/static/signatures/..."> that HTML references is what has to exist
here. Change the signature, change the filenames — no code change either way.

The signature shipped with the initial seed references three:

  <person>-photo.jpg      # 프로필 사진 (104x104, 정사각형; 원형은 CSS가 처리)
  g2-users-love-us.png    # G2 "Users Love Us" 배지 (높이 68px 기준)
  perso-dubbing-logo.png  # Perso Dubbing 로고 (높이 22px 기준)

A missing file shows a broken-image icon in that one spot; all text and layout render
fine either way, so a signature is never blocked on an image.

NOTE for sending real emails: email clients need ABSOLUTE image URLs. Set
PUBLIC_BASE_URL (e.g. https://<your-render-url>) so the send path can rewrite
/static/... to an absolute URL. In-app preview works with the relative path.
