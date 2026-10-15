slugify() should turn any text into a lowercase, hyphen-separated slug with punctuation removed.
It keeps punctuation: slugify('Hello, World!') returns 'hello,-world!'. Fix it so it returns 'hello-world'.
