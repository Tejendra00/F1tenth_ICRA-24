import cv2
import numpy as np

# Load PGM file
pgm_file = "/home/ros2/F1tenth-Final-Race-Agent-and-Toolbox/levine_0301.pgm"
image = cv2.imread(pgm_file, cv2.IMREAD_GRAYSCALE)

# Display original image
cv2.imshow("Original Image", image)
cv2.waitKey(0)

# Thresholding
_, binary_image = cv2.threshold(image, 127, 255, cv2.THRESH_BINARY_INV)

# Display binary image
cv2.imshow("Binary Image", binary_image)
cv2.waitKey(0)

# Contour Detection
contours, _ = cv2.findContours(binary_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

# Draw contours on original image
contour_image = np.copy(image)
cv2.drawContours(contour_image, contours, -1, (0, 255, 0), 2)

# Display contour image
cv2.imshow("Contour Image", contour_image)
cv2.waitKey(0)

# Extract Vertices
vertices_list = []
for contour in contours:
    contour = np.squeeze(contour)
    vertices = contour.tolist()
    vertices_list.append(vertices)

# Print or use the extracted vertices
for idx, vertices in enumerate(vertices_list):
    print(f"Obstacle {idx+1} vertices:", vertices)

# Wait for any key press and then close all windows
cv2.waitKey(0)
cv2.destroyAllWindows()

