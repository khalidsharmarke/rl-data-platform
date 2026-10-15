slugify() should turn any text into a lowercase, hyphen-separated slug.
It crashes on an empty string. Fix it so slugify('') returns ''.
